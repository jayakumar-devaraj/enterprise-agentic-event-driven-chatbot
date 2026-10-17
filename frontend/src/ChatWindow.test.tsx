// A8: the chat window renders tokens incrementally and shows the error and approval states.
// `fetch` is replaced by a labeled test double that streams SSE frames chunk by chunk.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ChatWindow } from "./ChatWindow";
import { parseFrames } from "./api";

function sse(events: object[]): string[] {
  return events.map((e, i) => `id: ${i + 1}-0\nevent: x\ndata: ${JSON.stringify(e)}\n\n`);
}

/** Test double for fetch: 202 for POSTs, then an SSE body released one chunk per `step()`. */
function fakeFetch(chunks: string[], postStatus = 202) {
  const encoder = new TextEncoder();
  const release: (() => void)[] = [];
  // Wait until the component is actually reading, then release exactly one chunk.
  const step = async () => {
    await waitFor(() => expect(release.length).toBeGreaterThan(0));
    release.shift()!();
  };
  const fetchImpl = (async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return new Response(
        JSON.stringify({ message_id: "m1", thread_id: "t1", stream_url: "/stream/t1/m1" }),
        { status: postStatus },
      );
    }
    let i = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        return new Promise<void>((resolve) => {
          release.push(() => {
            if (i < chunks.length) controller.enqueue(encoder.encode(chunks[i++]));
            else controller.close();
            resolve();
          });
        });
      },
    });
    return new Response(body, { status: 200 });
  }) as typeof fetch;
  return { fetchImpl, step };
}

async function send(text: string) {
  fireEvent.change(screen.getByLabelText("message"), { target: { value: text } });
  fireEvent.click(screen.getByText("Send"));
}

test("renders tokens incrementally until done", async () => {
  const { fetchImpl, step } = fakeFetch(
    sse([
      { type: "token", seq: 0, token: "Hello" },
      { type: "token", seq: 1, token: "there" },
      { type: "done", seq: 2 },
    ]),
  );
  render(<ChatWindow auth="Basic x" fetchImpl={fetchImpl} />);
  await send("hi");
  const assistant = () => screen.getAllByRole("listitem").at(-1)!;

  await act(step);
  await waitFor(() => expect(assistant().textContent).toContain("Hello"));
  expect(assistant().textContent).not.toContain("there");

  await act(step);
  await waitFor(() => expect(assistant().textContent).toContain("Hello there"));
  await act(step);
  await waitFor(() => expect((screen.getByText("Send") as HTMLButtonElement).disabled).toBe(false));
});

test("shows the error state on ErrorEvent", async () => {
  const { fetchImpl, step } = fakeFetch(
    sse([{ type: "error", seq: 0, code: "dependency_unavailable", detail: "x" }]),
  );
  render(<ChatWindow auth="Basic x" fetchImpl={fetchImpl} />);
  await send("hi");
  await act(step);
  expect((await screen.findByRole("alert")).textContent).toContain("temporarily unavailable");
});

test("shows the error state on an HTTP 429", async () => {
  const { fetchImpl } = fakeFetch([], 429);
  render(<ChatWindow auth="Basic x" fetchImpl={fetchImpl} />);
  await send("hi");
  expect((await screen.findByRole("alert")).textContent).toContain("Too many messages");
});

test("shows approve and reject on approval_required", async () => {
  const { fetchImpl, step } = fakeFetch(
    sse([{ type: "approval_required", seq: 0, action: "refund_request", detail: "Needs approval." }]),
  );
  render(<ChatWindow auth="Basic x" fetchImpl={fetchImpl} />);
  await send("refund please");
  await act(step);
  expect(await screen.findByText("Approve refund")).toBeTruthy();
  expect(screen.getByText("Reject")).toBeTruthy();
});

test("parseFrames keeps an incomplete frame for the next chunk", () => {
  const { frames, rest } = parseFrames('id: 1-0\ndata: {"a":1}\n\nid: 2-0\ndata: {"b"');
  expect(frames).toEqual([{ id: "1-0", data: '{"a":1}' }]);
  expect(rest).toBe('id: 2-0\ndata: {"b"');
});
