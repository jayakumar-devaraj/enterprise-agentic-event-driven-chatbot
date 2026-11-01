"""OIDC bearer-token validation for any standards-compliant issuer (ADR 0016)."""

import asyncio
import json
import urllib.request
from typing import Any

import jwt

# Asymmetric only: with HS* anyone holding the public key could forge tokens; "none" signs nothing.
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]


class InvalidToken(Exception):
    """The caller's token is missing, malformed, unsigned by the issuer, expired or not for us."""


class IssuerUnavailable(Exception):
    """The issuer's discovery document or keys could not be fetched (infrastructure, not caller)."""


class OidcVerifier:
    """Validates JWTs from one issuer: signature against its JWKS, `iss`, `aud`, `exp` and `nbf`.

    The JWKS location comes from the issuer's discovery document, fetched on first use. Keys are
    cached; a token signed with an unknown `kid` triggers a refetch at most once per cooldown_s, so
    key rotation needs no restart and forged `kid`s cannot make us hammer the issuer. Fetches are
    blocking urllib calls and run off the event loop.
    """

    def __init__(
        self,
        issuer: str,
        audience: str,
        user_claim: str = "sub",
        leeway_s: int = 30,
        cooldown_s: float = 30,
    ) -> None:
        """Bind to one issuer and audience; nothing is fetched yet."""
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._user_claim = user_claim
        self._leeway_s = leeway_s
        self._cooldown_s = cooldown_s
        self._jwks: jwt.PyJWKClient | None = None

    def _client(self) -> jwt.PyJWKClient:
        """The JWKS client, discovering `jwks_uri` on first call."""
        if self._jwks is None:
            url = f"{self._issuer}/.well-known/openid-configuration"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 (https issuer URL)
                    jwks_uri = json.load(resp)["jwks_uri"]
            except (OSError, ValueError, KeyError) as exc:
                raise IssuerUnavailable(f"discovery failed: {type(exc).__name__}") from exc
            self._jwks = jwt.PyJWKClient(
                jwks_uri,
                cache_keys=True,
                lifespan=300,
                timeout=10,
                cooldown_duration=self._cooldown_s,
            )
        return self._jwks

    def _verify(self, token: str) -> str:
        """Blocking validation; the user id from `user_claim`."""
        try:
            key = self._client().get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as exc:
            raise IssuerUnavailable("JWKS fetch failed") from exc
        except jwt.PyJWKClientError as exc:  # unknown kid even after a refetch
            raise InvalidToken("signing key not found") from exc
        except jwt.InvalidTokenError as exc:  # not a JWT at all
            raise InvalidToken("malformed token") from exc
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_s,
                options={"require": ["exp", "iss", "aud", self._user_claim]},
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidToken(type(exc).__name__) from exc
        user = claims[self._user_claim]
        if not isinstance(user, str) or not user:
            raise InvalidToken(f"claim {self._user_claim!r} is not a non-empty string")
        return user

    async def __call__(self, token: str) -> str:
        """The authenticated user id; InvalidToken (401) or IssuerUnavailable (503) otherwise."""
        return await asyncio.to_thread(self._verify, token)
