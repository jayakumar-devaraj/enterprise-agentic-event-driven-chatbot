// Browser login against an OIDC issuer: authorization code with PKCE (S256) and no client secret,
// so nothing secret ships in the bundle (ADR 0016). Tokens stay in memory; only the one-time PKCE
// verifier and state wait in sessionStorage across the redirect.

export interface OidcConfig {
  issuer: string;
  clientId: string;
  redirectUri: string;
}

export interface Tokens {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
}

interface Discovery {
  authorization_endpoint: string;
  token_endpoint: string;
}

const PENDING = "oidc.pending";

function base64url(bytes: Uint8Array): string {
  let text = "";
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(bytes: number): string {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

/** The S256 code challenge for `verifier` (RFC 7636 §4.2). */
export async function codeChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

async function discover(fetchImpl: typeof fetch, issuer: string): Promise<Discovery> {
  const res = await fetchImpl(`${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`);
  if (!res.ok) throw new Error(`identity provider unavailable (${res.status})`);
  return res.json();
}

/** Start a login: remember a fresh verifier and state, return the authorization URL to visit. */
export async function beginLogin(cfg: OidcConfig, fetchImpl: typeof fetch, storage: Storage): Promise<string> {
  const { authorization_endpoint } = await discover(fetchImpl, cfg.issuer);
  const verifier = randomString(32);
  const state = randomString(16);
  storage.setItem(PENDING, JSON.stringify({ verifier, state }));
  const url = new URL(authorization_endpoint);
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: cfg.clientId,
    redirect_uri: cfg.redirectUri,
    scope: "openid",
    state,
    code_challenge: await codeChallenge(verifier),
    code_challenge_method: "S256",
  }).toString();
  return url.toString();
}

async function token(cfg: OidcConfig, fetchImpl: typeof fetch, form: Record<string, string>): Promise<Tokens> {
  const { token_endpoint } = await discover(fetchImpl, cfg.issuer);
  const res = await fetchImpl(token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: cfg.clientId, ...form }).toString(),
  });
  if (!res.ok) throw new Error(`sign-in failed (${res.status})`);
  return res.json();
}

/**
 * Finish a login on the redirect back: null when the URL carries no authorization response. A
 * `state` that does not match the one this tab stored is refused (login CSRF), and the stored
 * verifier is used once.
 */
export async function completeLogin(
  cfg: OidcConfig,
  fetchImpl: typeof fetch,
  storage: Storage,
  search: string,
): Promise<Tokens | null> {
  const params = new URLSearchParams(search);
  const error = params.get("error");
  if (error) throw new Error(`sign-in refused: ${error}`);
  const code = params.get("code");
  if (!code) return null;
  const pending = storage.getItem(PENDING);
  storage.removeItem(PENDING);
  if (!pending) throw new Error("no sign-in in progress in this tab");
  const { verifier, state } = JSON.parse(pending) as { verifier: string; state: string };
  if (params.get("state") !== state) throw new Error("sign-in response does not match this tab");
  return token(cfg, fetchImpl, {
    grant_type: "authorization_code",
    code,
    redirect_uri: cfg.redirectUri,
    code_verifier: verifier,
  });
}

/** New tokens from a refresh token, before the access token expires. */
export function refreshTokens(cfg: OidcConfig, fetchImpl: typeof fetch, refreshToken: string): Promise<Tokens> {
  return token(cfg, fetchImpl, { grant_type: "refresh_token", refresh_token: refreshToken });
}
