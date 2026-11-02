# 0016 · OIDC bearer tokens at the gateway

**Status:** Accepted 2026-11-01.

## Context
The gateway authenticated callers with HTTP Basic against a scrypt table in `AUTH_USERS`. Every client
handles the user's password, users are managed by editing an environment variable, and nothing ties a
login to an organisation's identity provider, its MFA, or its offboarding. The lead chose a
provider-neutral design rather than one vendor.

## Decision
- With `OIDC_ISSUER` set, the gateway accepts only `Authorization: Bearer <JWT>` and turns Basic off.
  `OIDC_AUDIENCE` is required with it, and setting both `OIDC_ISSUER` and `AUTH_USERS` fails startup:
  one way in, no fallback.
- `gateway.oidc.OidcVerifier` reads `jwks_uri` from the issuer's discovery document on first use, checks
  the signature against the issuer's JWKS, and requires `iss`, `aud`, `exp` (30 s leeway) and the user
  claim (`OIDC_USER_CLAIM`, default `sub`). Only asymmetric algorithms are accepted, so `none` and the
  HS256-with-public-key forgery are refused. An unknown `kid` refetches the JWKS at most once per 30 s:
  rotation needs no restart, and forged `kid`s cannot make the gateway hammer the issuer.
- The user id from the token takes the place of the Basic username everywhere: thread ownership,
  `USER_TENANTS`, audit actor. Users are not known at startup, so `USER_TENANTS` is no longer checked
  against a user table in this mode.
- Invalid or missing tokens are 401 with a `Bearer` challenge; an unreachable issuer is 503 (an
  infrastructure fault, not the caller's).

## Consequences
- Works with Entra ID, Okta, Auth0, Keycloak or Google by configuration alone; scopes, roles and group
  claims are not used yet.
- Revocation is bounded by token lifetime: a revoked user's unexpired token is accepted until `exp`.
- The browser client signed in with Basic only; see the addenda for its PKCE login and the local issuer.

## Addendum 2026-11-01: a local issuer
Compose profile `oidc` runs Keycloak 26.8.1 in development mode with realm `chatbot`
(`deploy/keycloak/realm.json`): `chatbot-web` (public, authorization code with PKCE S256, for the browser),
`chatbot-test` (password grant, for automated tests only) and `other-app` (no gateway audience). The first
two add `chatbot-gateway` to `aud`. No user or password is committed: `keycloak-init` creates `demo` with
`KEYCLOAK_DEMO_PASSWORD`. `tests/integration/test_oidc_keycloak.py` (marker `oidc`, its own CI job) takes
real tokens from Keycloak to the gateway over real Redis and Kafka: the gateway's token is accepted and
its subject owns the thread, another application's token is 401, and Basic is off. The realm's password
grant client exists only for tests and must not be imported into a production realm.

## Addendum 2026-11-02: the browser signs in
With `VITE_OIDC_ISSUER` set (compose passes `OIDC_ISSUER`), the chat window replaces the Basic form with
"Sign in": authorization code with PKCE S256 and no client secret (`frontend/src/oidc.ts`, WebCrypto, no
library). The verifier and `state` wait in `sessionStorage` across the redirect and are used once; a
response whose `state` is not the tab's is refused. Tokens stay in memory and are refreshed 30 s before
expiry. In compose the gateway reaches Keycloak at `keycloak:8080` while browsers and tokens use
`localhost:18080`, so `OIDC_DISCOVERY_URL` names where the gateway fetches discovery while `iss` is still
checked against `OIDC_ISSUER`.
