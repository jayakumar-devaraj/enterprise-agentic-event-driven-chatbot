import { useRef, useState } from "react";
import { HttpError, postApprove, postChat, streamMessage, type Fetch, type StreamEvent } from "./api";

interface Message {
  role: "user" | "assistant";
  text: string;
}

type Status = "idle" | "sending" | "streaming" | "awaiting_approval" | "error";

const HTTP_MESSAGES: Record<number, string> = {
  401: "Your session is not authorised. Please sign in again.",
  403: "This conversation belongs to another user.",
  413: "That message is too long.",
  422: "That message could not be accepted.",
  429: "Too many messages. Please wait a moment.",
  503: "The service is temporarily unavailable. Please try again.",
};

function describe(err: unknown): string {
  if (err instanceof HttpError) return HTTP_MESSAGES[err.status] ?? `Request failed (${err.status}).`;
  return "The connection was interrupted. Please try again.";
}

export function ChatWindow({ auth, fetchImpl = fetch }: { auth: string; fetchImpl?: Fetch }) {
  const [threadId] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const pendingRef = useRef<string | null>(null);

  function appendToken(token: string) {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      const text = last.text ? `${last.text} ${token}` : token;
      return [...prev.slice(0, -1), { ...last, text }];
    });
  }

  async function run(accepted: Promise<{ stream_url: string; message_id: string }>) {
    try {
      const { stream_url, message_id } = await accepted;
      setStatus("streaming");
      setMessages((prev) => [...prev, { role: "assistant", text: "" }]);
      await streamMessage(fetchImpl, auth, stream_url, (event: StreamEvent) => {
        if (event.type === "token") appendToken(event.token);
        else if (event.type === "done") setStatus("idle");
        else if (event.type === "error") {
          setError(event.code === "dependency_unavailable" ? HTTP_MESSAGES[503] : event.detail);
          setStatus("error");
        } else {
          pendingRef.current = message_id;
          appendToken(event.detail);
          setStatus("awaiting_approval");
        }
      }, () => setMessages((prev) => [...prev.slice(0, -1), { role: "assistant", text: "" }]));
    } catch (err) {
      setError(describe(err));
      setStatus("error");
    }
  }

  function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || status === "sending" || status === "streaming") return;
    setInput("");
    setError(null);
    setStatus("sending");
    setMessages((prev) => [...prev, { role: "user", text }]);
    void run(postChat(fetchImpl, auth, threadId, text));
  }

  function decide(approved: boolean) {
    const ref = pendingRef.current;
    if (!ref) return;
    pendingRef.current = null;
    setStatus("sending");
    void run(postApprove(fetchImpl, auth, threadId, ref, approved));
  }

  return (
    <main className="chat">
      <ul aria-label="messages">
        {messages.map((m, i) => (
          <li key={i} data-role={m.role}>
            <strong>{m.role === "user" ? "You" : "Assistant"}:</strong> <span>{m.text}</span>
          </li>
        ))}
      </ul>
      {error && (
        <div role="alert">
          {error} <button onClick={() => { setError(null); setStatus("idle"); }}>Dismiss</button>
        </div>
      )}
      {status === "awaiting_approval" && (
        <div role="group" aria-label="approval">
          <button onClick={() => decide(true)}>Approve refund</button>
          <button onClick={() => decide(false)}>Reject</button>
        </div>
      )}
      <form onSubmit={send}>
        <input aria-label="message" value={input} onChange={(e) => setInput(e.target.value)} />
        <button type="submit" disabled={status === "sending" || status === "streaming"}>
          Send
        </button>
      </form>
    </main>
  );
}
