"""E2A's idempotency and checkpoint ports, bound from a profile, on e2a-adapters.

The claim state machine (ABSENT -> IN_PROGRESS -> SUCCEEDED | FAILED_* | UNKNOWN) and the
decide-once checkpoint are NOT implemented here. They come from `e2a-adapters` (shipped with
E2A v1.5.0): DynamoDB, Firestore and Cosmos DB adapters that pass E2A's port contract kit
(`e2a.port_contract`), races included, on DynamoDB Local and the Firestore and Cosmos DB
emulators. This package only binds them the way every seam is bound, `Class(config=...)`,
from its own configuration keys.

Construction is lazy: the adapter, its SDK client and `e2a_adapters` itself are built on the
first call, so `Bindings.verify()` and offline runs need no credentials, and a project venv
that never touches a store does not need the store's SDK.

Each cloud subclass implements one method, `_build()`, returning the e2a-adapters store.
"""
import time
from typing import Any, Callable, Dict, FrozenSet, Optional

from e2a import BaseCheckpointStore, BaseIdempotencyStore

from .capabilities import Capability


class _Lazy:
    def __init__(self, config: Optional[Dict[str, Any]], clock: Callable[[], float], **injected: Any) -> None:
        self.config, self._clock, self._injected, self._built = dict(config or {}), clock, injected, None

    def _build(self) -> Any:                     # pragma: no cover - every cloud implements it
        raise NotImplementedError

    def _adapter(self) -> Any:
        if self._built is None:
            self._built = self._build()
        return self._built


class AdapterIdempotencyStore(_Lazy, BaseIdempotencyStore):
    capabilities: FrozenSet[str] = frozenset({Capability.IDEMPOTENCY_STORE})

    def __init__(self, config: Optional[Dict[str, Any]] = None, clock: Callable[[], float] = time.time,
                 **injected: Any) -> None:
        _Lazy.__init__(self, config, clock, **injected)

    def claim(self, idempotency_key, config=None, **kwargs):
        return self._adapter().claim(idempotency_key, config, **kwargs)

    def complete(self, idempotency_key, result, config=None, **kwargs):
        return self._adapter().complete(idempotency_key, result, config, **kwargs)

    def fail(self, idempotency_key, retryable=True, config=None, **kwargs):
        return self._adapter().fail(idempotency_key, retryable, config, **kwargs)

    def state_of(self, key, tenant_id=None):
        return self._adapter().state_of(key, tenant_id)


class AdapterCheckpointStore(_Lazy, BaseCheckpointStore):
    capabilities: FrozenSet[str] = frozenset({Capability.CHECKPOINT_STORE})

    def __init__(self, config: Optional[Dict[str, Any]] = None, clock: Callable[[], float] = time.time,
                 **injected: Any) -> None:
        _Lazy.__init__(self, config, clock, **injected)

    def save(self, checkpoint, config=None, **kwargs):
        return self._adapter().save(checkpoint, config, **kwargs)

    def load(self, checkpoint_id, config=None, **kwargs):
        return self._adapter().load(checkpoint_id, config, **kwargs)

    def decide(self, checkpoint_id, status, decided_by, config=None, **kwargs):
        return self._adapter().decide(checkpoint_id, status, decided_by, config, **kwargs)
