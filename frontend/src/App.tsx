import { useEffect, useRef, useState } from "react";
import { basicAuth } from "./api";
import { ChatWindow } from "./ChatWindow";
import { beginLogin, completeLogin, refreshTokens, type OidcConfig, type Tokens } from "./oidc";

// With VITE_OIDC_ISSUER set the window signs in through that issuer (ADR 0016); otherwise HTTP Basic.
const OIDC: OidcConfig | null = import.meta.env.VITE_OIDC_ISSUER
  ? {
      issuer: import.meta.env.VITE_OIDC_ISSUER,
      clientId: import.meta.env.VITE_OIDC_CLIENT_ID ?? "chatbot-web",
      redirectUri: `${window.location.origin}/`,
    }
  : null;

export function App() {
  const [auth, setAuth] = useState<string | null>(null);
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [tokens, setTokens] = useState<Tokens | null>(null);
  const [error, setError] = useState<string | null>(null);
  const completing = useRef(false); // StrictMode runs effects twice; the code is single-use

  useEffect(() => {
    if (!OIDC || completing.current) return;
    completing.current = true;
    completeLogin(OIDC, fetch, sessionStorage, window.location.search)
      .then((t) => {
        if (!t) return;
        window.history.replaceState(null, "", "/");
        setTokens(t);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!OIDC || !tokens) return;
    setAuth(`Bearer ${tokens.access_token}`);
    const refreshToken = tokens.refresh_token;
    if (!refreshToken) return;
    const timer = setTimeout(
      () =>
        refreshTokens(OIDC, fetch, refreshToken)
          .then(setTokens)
          .catch(() => {
            setTokens(null);
            setAuth(null);
            setError("session expired; sign in again");
          }),
      Math.max(tokens.expires_in - 30, 5) * 1000,
    );
    return () => clearTimeout(timer);
  }, [tokens]);

  if (auth) return <ChatWindow auth={auth} />;
  if (OIDC) {
    const config = OIDC;
    return (
      <div className="login">
        <h1>Support chat</h1>
        {error && <p role="alert">{error}</p>}
        <button
          type="button"
          onClick={() =>
            beginLogin(config, fetch, sessionStorage)
              .then((url) => window.location.assign(url))
              .catch((e: Error) => setError(e.message))
          }
        >
          Sign in
        </button>
      </div>
    );
  }
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
