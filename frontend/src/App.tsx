import { useState } from "react";
import { basicAuth } from "./api";
import { ChatWindow } from "./ChatWindow";

export function App() {
  const [auth, setAuth] = useState<string | null>(null);
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");

  if (auth) return <ChatWindow auth={auth} />;
  return (
    <form
      className="login"
      onSubmit={(e) => {
        e.preventDefault();
        setAuth(basicAuth(user, password));
      }}
    >
      <h1>Support chat</h1>
      <input aria-label="username" value={user} onChange={(e) => setUser(e.target.value)} />
      <input
        aria-label="password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <button type="submit">Sign in</button>
    </form>
  );
}
