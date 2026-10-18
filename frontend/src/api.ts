// Gateway client: POST /chat, POST /approve, and an SSE reader built on fetch (EventSource cannot
// send the Basic Authorization header or a Last-Event-ID on the first request).

export type StreamEvent =
  | { type: "token"; seq: number; token: string }
  | { type: "done"; seq: number }
  | { type: "error"; seq: number; code: string; detail: string }
  | { type: "approval_required"; seq: number; action: string; detail: string };

export interface Accepted {
  message_id: string;
  thread_id: string;
  stream_url: string;
}

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type Fetch = typeof fetch;
const BASE = "/api";

/** Basic auth header value; credentials stay in memory only. */
export function basicAuth(user: string, password: string): string {
  return "Basic " + btoa(`${user}:${password}`);
}

/**
 * POST once more on an uncertain outcome (network failure or 503). The body, and so its
 * message_id, is identical, so the gateway either produces it or answers with the original 202.
 */
async function postJson(fetchImpl: Fetch, path: string, auth: string, body: unknown): Promise<Accepted> {
  const init = {
    method: "POST",
    headers: { Authorization: auth, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  for (let attempt = 0; ; attempt++) {
    let res: Response;
    try {
      res = await fetchImpl(`${BASE}${path}`, init);
    } catch (err) {
      if (attempt === 1) throw err;
      continue;
    }
    if (res.status === 503 && attempt === 0) continue;
    if (res.status !== 202) {
      throw new HttpError(res.status, await res.text());
    }
    return (await res.json()) as Accepted;
  }
}

export function postChat(fetchImpl: Fetch, auth: string, threadId: string, message: string) {
  return postJson(fetchImpl, "/chat", auth, {
    message,
    thread_id: threadId,
    message_id: crypto.randomUUID(),
  });
}

export function postApprove(fetchImpl: Fetch, auth: string, threadId: string, refId: string, approved: boolean) {
  return postJson(fetchImpl, `/approve/${threadId}/${refId}`, auth, {
    message_id: crypto.randomUUID(),
    approved,
  });
}

/** Parse complete SSE frames from `buffer`; returns parsed frames and the unconsumed remainder. */
export function parseFrames(buffer: string): { frames: { id?: string; data: string }[]; rest: string } {
  const frames: { id?: string; data: string }[] = [];
  const parts = buffer.split(/\r?\n\r?\n/);
  const rest = parts.pop() ?? "";
  for (const part of parts) {
    let id: string | undefined;
    const data: string[] = [];
    for (const line of part.split(/\r?\n/)) {
      if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length) frames.push({ id, data: data.join("\n") });
  }
  return { frames, rest };
}

/**
 * Stream one message's events. On a dropped connection it resumes once with Last-Event-ID
 * (the server guarantees no gaps or duplicates); duplicate or out-of-order seq values are ignored.
 */
export async function streamMessage(
  fetchImpl: Fetch,
  auth: string,
  streamUrl: string,
  onEvent: (e: StreamEvent) => void,
): Promise<void> {
  let lastId: string | undefined;
  let nextSeq = 0;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const headers: Record<string, string> = { Authorization: auth };
      if (lastId) headers["Last-Event-ID"] = lastId;
      const res = await fetchImpl(`${BASE}${streamUrl}`, { headers });
      if (!res.ok || !res.body) throw new HttpError(res.status, "stream unavailable");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = parseFrames(buffer);
        buffer = rest;
        for (const frame of frames) {
          const event = JSON.parse(frame.data) as StreamEvent;
          lastId = frame.id ?? lastId;
          if (event.seq !== nextSeq) continue;
          nextSeq += 1;
          onEvent(event);
          if (event.type !== "token") return;
        }
      }
      throw new Error("stream ended before a terminal event");
    } catch (err) {
      if (err instanceof HttpError || attempt === 1) throw err;
    }
  }
}
