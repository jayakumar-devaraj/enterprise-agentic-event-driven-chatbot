// Uncertain-delivery retries reuse the original message_id, so the gateway can deduplicate them.
import { postApprove, postChat, streamMessage, type StreamEvent } from "./api";

const ACCEPTED = JSON.stringify({ message_id: "m1", thread_id: "t1", stream_url: "/stream/t1/m1" });

/** Test double for fetch: replays `outcomes` in order and records each POST body. */
function scripted(outcomes: (number | "network")[]) {
  const bodies: { message_id: string }[] = [];
  const fetchImpl = (async (_url: string, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body)));
    const next = outcomes.shift();
    if (next === "network") throw new TypeError("Failed to fetch");
    return new Response(next === 202 ? ACCEPTED : "down", { status: next });
  }) as typeof fetch;
  return { fetchImpl, bodies };
}

test.each([["network"], [503]] as const)("retries once after %s with the same message_id", async (first) => {
  const { fetchImpl, bodies } = scripted([first, 202]);
  await postChat(fetchImpl, "Basic x", "t1", "hello");
  expect(bodies).toHaveLength(2);
  expect(bodies[1].message_id).toBe(bodies[0].message_id);
});

test("approval retry keeps its message_id; a second failure surfaces", async () => {
  const ok = scripted([503, 202]);
  await postApprove(ok.fetchImpl, "Basic x", "t1", "r1", true);
  expect(ok.bodies[1].message_id).toBe(ok.bodies[0].message_id);

  const down = scripted([503, 503]);
  await expect(postChat(down.fetchImpl, "Basic x", "t1", "hello")).rejects.toMatchObject({ status: 503 });
  expect(down.bodies).toHaveLength(2);
});

test("caller errors are not retried", async () => {
  const { fetchImpl, bodies } = scripted([422]);
  await expect(postChat(fetchImpl, "Basic x", "t1", "hello")).rejects.toMatchObject({ status: 422 });
  expect(bodies).toHaveLength(1);
});

test("a stream restarted at seq 0 discards the partial attempt", async () => {
  const events = [
    { type: "token", seq: 0, token: "half" },
    { type: "token", seq: 1, token: "way" },
    { type: "token", seq: 0, token: "full" },
    { type: "token", seq: 1, token: "answer" },
    { type: "done", seq: 2 },
  ];
  const body = events.map((e, i) => `id: ${i + 1}-0\ndata: ${JSON.stringify(e)}\n\n`).join("");
  const fetchImpl = (async () => new Response(body, { status: 200 })) as unknown as typeof fetch;
  const seen: string[] = [];
  let restarts = 0;
  await streamMessage(
    fetchImpl,
    "Basic x",
    "/stream/t1/m1",
    (e: StreamEvent) => seen.push(e.type === "token" ? e.token : e.type),
    () => {
      restarts += 1;
      seen.length = 0;
    },
  );
  expect(restarts).toBe(1);
  expect(seen).toEqual(["full", "answer", "done"]);
});

test("a status event is progress: the stream is read on to its terminal event", async () => {
  const events = [
    { type: "status", seq: 0, detail: "Searching your documents..." },
    { type: "token", seq: 1, token: "hi" },
    { type: "citations", seq: 2, citations: [{ doc_key: "a.txt", page_key: "page:1" }] },
    { type: "done", seq: 3 },
  ];
  const body = events.map((e, i) => `id: ${i + 1}-0\ndata: ${JSON.stringify(e)}\n\n`).join("");
  const fetchImpl = (async () => new Response(body, { status: 200 })) as unknown as typeof fetch;
  const seen: string[] = [];
  await streamMessage(fetchImpl, "Basic x", "/stream/t1/m1", (e: StreamEvent) => seen.push(e.type));
  expect(seen).toEqual(["status", "token", "citations", "done"]);
});
