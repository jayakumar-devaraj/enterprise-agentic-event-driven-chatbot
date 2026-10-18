"""Labeled test doubles shared by unit tests: an in-memory Redis subset and a recording producer."""

import time
from typing import Any

from chat_common.schemas import Envelope
from gateway.producer import DependencyUnavailable
from redis.exceptions import ResponseError
from worker import lease, streams


class FakeRedis:
    """Test double implementing the Redis commands the services use (decode_responses=True)."""

    def __init__(self) -> None:
        """Empty keyspace."""
        self.kv: dict[str, str] = {}
        self.expiry: dict[str, float] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.fail = False

    def _check(self) -> None:
        """Simulate an outage when `fail` is set."""
        if self.fail:
            from redis.exceptions import ConnectionError

            raise ConnectionError("fake outage")

    def _live(self, key: str) -> bool:
        """Expire lazily."""
        if key in self.expiry and self.expiry[key] < time.time():
            self.kv.pop(key, None)
            self.expiry.pop(key, None)
        return key in self.kv

    async def ping(self) -> bool:
        """Health probe."""
        self._check()
        return True

    async def set(
        self, key: str, value: str, nx: bool = False, ex: int | None = None, xx: bool = False
    ) -> bool | None:
        """SET with NX/XX/EX semantics."""
        self._check()
        exists = self._live(key)
        if (nx and exists) or (xx and not exists):
            return None
        self.kv[key] = str(value)
        if ex is not None:
            self.expiry[key] = time.time() + ex
        return True

    async def get(self, key: str) -> str | None:
        """GET."""
        self._check()
        return self.kv[key] if self._live(key) else None

    async def delete(self, *keys: str) -> int:
        """DEL."""
        self._check()
        return sum(self.kv.pop(k, None) is not None for k in keys)

    async def incr(self, key: str) -> int:
        """INCR."""
        self._check()
        value = int(self.kv.get(key, "0")) + 1 if self._live(key) else 1
        self.kv[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        """EXPIRE."""
        self._check()
        self.expiry[key] = time.time() + seconds
        return True

    async def xadd(self, name: str, fields: dict[str, Any], **_: Any) -> str:
        """XADD with monotonically increasing ids."""
        self._check()
        rows = self.streams.setdefault(name, [])
        entry_id = f"{len(rows) + 1}-0"
        rows.append((entry_id, {k: str(v) for k, v in fields.items()}))
        return entry_id

    async def xrevrange(self, name: str, max: str = "+", min: str = "-", count: int | None = None):
        """XREVRANGE."""
        self._check()
        return list(reversed(self.streams.get(name, [])))[:count]

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        """Emulate the worker's fenced scripts (ADR 0009); real Lua runs in integration tests."""
        self._check()
        keys, argv = list(args[:numkeys]), list(args[numkeys:])
        if (self.kv[keys[-1]] if self._live(keys[-1]) else None) != argv[-1]:
            raise ResponseError(f"LEASE_LOST {keys[-1]}")
        if script == streams._ADD:
            entry_id = await self.xadd(keys[0], {"data": argv[1]})
            await self.expire(keys[0], int(argv[2]))
            return entry_id
        if script in (streams._RESET, lease._RELEASE):
            self.streams.pop(keys[0], None)
            return await self.delete(keys[0])
        if script == lease._DONE:
            return await self.set(keys[0], "done", ex=int(argv[0]))
        raise NotImplementedError("script not emulated by FakeRedis")

    async def aclose(self) -> None:
        """No-op close."""


class RecordingProducer:
    """Test double for the Kafka producer: records envelopes, can simulate an outage."""

    def __init__(self) -> None:
        """No messages yet."""
        self.sent: list[Envelope] = []
        self.fail = False

    async def send(self, envelope: Envelope) -> None:
        """Record or fail."""
        if self.fail:
            raise DependencyUnavailable("fake kafka outage")
        self.sent.append(envelope)

    async def ready(self) -> None:
        """Ready unless an outage is simulated."""
        if self.fail:
            raise DependencyUnavailable("fake kafka outage")

    async def close(self) -> None:
        """No-op close."""
