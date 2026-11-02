// PKCE login against a scripted issuer: challenge, authorization URL, state check, code exchange.
import { beginLogin, codeChallenge, completeLogin, refreshTokens, type OidcConfig } from "./oidc";

const CFG: OidcConfig = { issuer: "https://idp.test/realms/r", clientId: "web", redirectUri: "http://app.test/" };
const DISCOVERY = { authorization_endpoint: "https://idp.test/auth", token_endpoint: "https://idp.test/token" };
const TOKENS = { access_token: "at", refresh_token: "rt", expires_in: 300 };

/** Test double for fetch: discovery and token endpoint; records token request bodies. */
function issuer(tokenStatus = 200) {
  const forms: URLSearchParams[] = [];
  const fetchImpl = (async (url: string, init?: RequestInit) => {
    if (url.endsWith("/.well-known/openid-configuration")) return Response.json(DISCOVERY);
    forms.push(new URLSearchParams(String(init?.body)));
    return tokenStatus === 200 ? Response.json(TOKENS) : new Response("no", { status: tokenStatus });
  }) as typeof fetch;
  return { fetchImpl, forms };
}

beforeEach(() => sessionStorage.clear());

test("the S256 challenge matches RFC 7636's example", async () => {
  expect(await codeChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
    "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
  );
});

test("login sends PKCE S256 and state, and the callback exchanges the code with that verifier", async () => {
  const { fetchImpl, forms } = issuer();
  const url = new URL(await beginLogin(CFG, fetchImpl, sessionStorage));
  expect(url.origin + url.pathname).toBe("https://idp.test/auth");
  const q = url.searchParams;
  expect([q.get("response_type"), q.get("client_id"), q.get("redirect_uri"), q.get("code_challenge_method")]).toEqual([
    "code",
    "web",
    "http://app.test/",
    "S256",
  ]);
  const { verifier } = JSON.parse(sessionStorage.getItem("oidc.pending")!);
  expect(q.get("code_challenge")).toBe(await codeChallenge(verifier));

  const tokens = await completeLogin(CFG, fetchImpl, sessionStorage, `?code=c1&state=${q.get("state")}`);
  expect(tokens).toEqual(TOKENS);
  expect(Object.fromEntries(forms[0])).toEqual({
    client_id: "web",
    grant_type: "authorization_code",
    code: "c1",
    redirect_uri: "http://app.test/",
    code_verifier: verifier,
  });
  expect(sessionStorage.getItem("oidc.pending")).toBeNull(); // the verifier is used once
});

test("a response whose state is not this tab's is refused, and nothing is exchanged", async () => {
  const { fetchImpl, forms } = issuer();
  await beginLogin(CFG, fetchImpl, sessionStorage);
  await expect(completeLogin(CFG, fetchImpl, sessionStorage, "?code=c1&state=forged")).rejects.toThrow(/does not match/);
  expect(forms).toHaveLength(0);
});

test("no authorization response means no login; an issuer error is reported", async () => {
  const { fetchImpl } = issuer();
  expect(await completeLogin(CFG, fetchImpl, sessionStorage, "")).toBeNull();
  await expect(completeLogin(CFG, fetchImpl, sessionStorage, "?error=access_denied")).rejects.toThrow(/access_denied/);
  await expect(completeLogin(CFG, issuer(400).fetchImpl, sessionStorage, "?code=c&state=s")).rejects.toThrow(
    /no sign-in in progress/,
  );
});

test("refresh posts the refresh token grant", async () => {
  const { fetchImpl, forms } = issuer();
  expect(await refreshTokens(CFG, fetchImpl, "rt")).toEqual(TOKENS);
  expect(forms[0].get("grant_type")).toBe("refresh_token");
  expect(forms[0].get("refresh_token")).toBe("rt");
});
