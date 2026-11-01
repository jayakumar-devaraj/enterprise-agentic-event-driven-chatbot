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
- The browser client still sends Basic credentials; it needs an authorization-code-with-PKCE login
  before an OIDC deployment can serve it, and a local issuer (Keycloak) is needed to run one end to end.
