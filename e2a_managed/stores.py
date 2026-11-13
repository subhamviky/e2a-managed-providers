"""Durable idempotency and checkpoint stores on managed databases.

E2A's claim state machine (ABSENT -> IN_PROGRESS -> SUCCEEDED | FAILED_* | UNKNOWN)
needs exactly one primitive from a database: an atomic conditional write. Every
cloud's managed document store has one:

    DynamoDB   PutItem/UpdateItem with ConditionExpression on a version attribute
    Cosmos DB  replace with If-Match ETag (optimistic concurrency), create = insert-if-absent
    Firestore  create() (fails if present) / update() with a last-update-time precondition

So the state machine is written ONCE here, over two primitives; each cloud implements
only _read and _write_if. That keeps the hard part (lease takeover, fingerprint conflict,
replay, recovery) identical across clouds, and the in-memory reference adapter's tests
apply to all of them.
"""
import abc
import time
import uuid
from typing import Any, Dict, FrozenSet, Optional, Tuple

from e2a import BaseCheckpointStore, BaseIdempotencyStore, ClaimResult, ClaimStatus, PhysicalOutcome

from .capabilities import Capability

Record = Dict[str, Any]


class ConditionalWriteStore(abc.ABC):
    """version None = the record must not exist yet."""

    @abc.abstractmethod
    def _read(self, key: str) -> Tuple[Optional[Record], Optional[Any]]:
        """(record, version) or (None, None)."""

    @abc.abstractmethod
    def _write_if(self, key: str, record: Record, expected_version: Optional[Any]) -> bool:
        """Atomic conditional write; False when the version no longer matches."""

    def _clock(self) -> float:
        return time.time()                 # wall clock: leases are compared across processes


class ConditionalIdempotencyStore(ConditionalWriteStore, BaseIdempotencyStore):
    capabilities: FrozenSet[str] = frozenset({Capability.IDEMPOTENCY_STORE})
    max_cas_attempts = 5

    @staticmethod
    def _key(key: str, kw: Dict[str, Any]) -> str:
        return f"{kw.get('tenant_id') or '-'}:{key}"

    def claim(self, idempotency_key, config=None, **kw) -> ClaimResult:
        k = self._key(idempotency_key, kw)
        fp, owner = kw.get("fingerprint"), kw.get("owner") or str(uuid.uuid4())
        lease, ttl = float(kw.get("lease_seconds") or 300), kw.get("ttl_seconds")
        for _ in range(self.max_cas_attempts):
            now = self._clock()
            rec, ver = self._read(k)
            if rec and rec.get("expires_at") and now >= rec["expires_at"]:
                rec = None                                   # explicit expiry, never store-TTL timing
            fresh = {"state": "IN_PROGRESS", "owner": owner, "lease_until": now + lease, "fp": fp,
                     "result": None, "expires_at": (now + float(ttl)) if ttl else None}
            if rec is None or rec["state"] == "FAILED_RETRYABLE":
                if self._write_if(k, fresh, ver):
                    return ClaimResult(ClaimStatus.CLAIMED, owner=owner)
                continue                                     # lost the race: re-read and re-decide
            if fp is not None and rec.get("fp") is not None and fp != rec["fp"]:
                return ClaimResult(ClaimStatus.CONFLICT)
            if rec["state"] == "SUCCEEDED":
                return ClaimResult(ClaimStatus.REPLAY, result=rec.get("result"))
            if rec["state"] == "FAILED_FINAL":
                return ClaimResult(ClaimStatus.FAILED_FINAL)
            expired = rec["state"] == "IN_PROGRESS" and now >= float(rec["lease_until"])
            if rec["state"] == "UNKNOWN" or expired:
                if kw.get("recover"):
                    taken = dict(rec, state="IN_PROGRESS", owner=owner, lease_until=now + lease)
                    if self._write_if(k, taken, ver):
                        return ClaimResult(ClaimStatus.CLAIMED, owner=owner)
                    continue
                return ClaimResult(ClaimStatus.RECOVERY_REQUIRED)
            return ClaimResult(ClaimStatus.IN_PROGRESS, owner=rec["owner"])
        return ClaimResult(ClaimStatus.IN_PROGRESS)          # persistent contention: behave as busy

    def _transition(self, idempotency_key: str, kw: Dict[str, Any], **changes: Any) -> bool:
        k = self._key(idempotency_key, kw)
        for _ in range(self.max_cas_attempts):
            rec, ver = self._read(k)
            if not rec or rec["state"] != "IN_PROGRESS" or rec["owner"] != kw.get("owner"):
                return False                                 # stale owner: its lease was taken over
            if self._write_if(k, dict(rec, **changes), ver):
                return True
        return False

    def complete(self, idempotency_key, result, config=None, **kw) -> bool:
        return self._transition(idempotency_key, kw, state="SUCCEEDED", result=result)

    def fail(self, idempotency_key, retryable=True, config=None, **kw) -> bool:
        state = "UNKNOWN" if kw.get("outcome") == PhysicalOutcome.UNKNOWN else \
            ("FAILED_RETRYABLE" if retryable else "FAILED_FINAL")
        return self._transition(idempotency_key, kw, state=state)


class ConditionalCheckpointStore(ConditionalWriteStore, BaseCheckpointStore):
    capabilities: FrozenSet[str] = frozenset({Capability.CHECKPOINT_STORE})

    def save(self, checkpoint, config=None, **kwargs) -> None:
        if not self._write_if(checkpoint["checkpoint_id"], dict(checkpoint), None):
            raise ValueError(f"checkpoint {checkpoint['checkpoint_id']} already exists")

    def load(self, checkpoint_id, config=None, **kwargs):
        rec, _ = self._read(checkpoint_id)
        if rec is None or (kwargs.get("tenant_id") and rec.get("tenant_id") != kwargs["tenant_id"]):
            return None
        return rec

    def decide(self, checkpoint_id, status, decided_by, config=None, **kwargs) -> bool:
        rec, ver = self._read(checkpoint_id)
        if rec is None or rec.get("status") != "PENDING":
            return False
        if kwargs.get("tenant_id") and rec.get("tenant_id") != kwargs["tenant_id"]:
            return False
        return self._write_if(checkpoint_id, dict(rec, status=status, decided_by=decided_by,
                                                  decided_at=self._clock()), ver)
