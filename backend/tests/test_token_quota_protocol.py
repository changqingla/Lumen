import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest

from config.quota_config import (
    UserLevel,
    get_billing_window,
    get_effective_user_level,
)
from schemas.token_usage import RuntimeTokenUsageEnvelope, RuntimeTokenUsageEvent
from utils import token_quota_ledger as ledger_module
from utils import token_usage_queue as queue_module
from utils.token_quota_ledger import TokenQuotaLedger
from utils.token_usage_context import (
    InvalidUsageContext,
    UsageContextClaims,
    create_usage_context,
    decode_usage_context,
    sign_queue_payload,
)


class InMemoryQuotaRedis:
    """Minimal atomic Redis/Lua model for quota transition tests."""

    def __init__(self):
        self.expiries = {}
        self.amounts = {}
        self.committed = {}
        self.settled = {}
        self._lock = asyncio.Lock()

    def _cleanup(self, expiry_key, amount_key, now):
        expiries = self.expiries.setdefault(expiry_key, {})
        amounts = self.amounts.setdefault(amount_key, {})
        for reservation_id, expires_at in list(expiries.items()):
            if expires_at <= now:
                expiries.pop(reservation_id, None)
                amounts.pop(reservation_id, None)

    async def eval(self, script, key_count, *args):
        keys = args[:key_count]
        argv = args[key_count:]
        async with self._lock:
            if script == ledger_module._RESERVE_SCRIPT:
                expiry_key, amount_key, committed_key = keys
                now, db_total, requested, limit = map(int, argv[:4])
                reservation_id, expires_at = str(argv[4]), int(argv[5])
                self._cleanup(expiry_key, amount_key, now)
                committed = max(int(self.committed.get(committed_key, 0)), db_total)
                self.committed[committed_key] = committed
                amounts = self.amounts.setdefault(amount_key, {})
                pending = sum(amounts.values())
                if committed + pending + requested > limit:
                    return [0, committed, pending]
                amounts[reservation_id] = requested
                self.expiries.setdefault(expiry_key, {})[reservation_id] = expires_at
                return [1, committed, pending + requested]

            if script == ledger_module._SNAPSHOT_SCRIPT:
                expiry_key, amount_key, committed_key = keys
                now, db_total = int(argv[0]), int(argv[1])
                self._cleanup(expiry_key, amount_key, now)
                committed = max(int(self.committed.get(committed_key, 0)), db_total)
                self.committed[committed_key] = committed
                return [committed, sum(self.amounts.setdefault(amount_key, {}).values())]

            if script == ledger_module._SETTLE_SCRIPT:
                expiry_key, amount_key, committed_key, settled_key = keys
                now = int(argv[0])
                event_id, reservation_id = str(argv[1]), str(argv[2])
                actual, db_total = int(argv[3]), int(argv[4])
                self._cleanup(expiry_key, amount_key, now)
                markers = self.settled.setdefault(settled_key, set())
                first = int(event_id not in markers)
                if first:
                    markers.add(event_id)
                    amounts = self.amounts.setdefault(amount_key, {})
                    remaining = int(amounts.get(reservation_id, 0))
                    new_remaining = remaining - min(remaining, actual)
                    if new_remaining:
                        amounts[reservation_id] = new_remaining
                    else:
                        amounts.pop(reservation_id, None)
                        self.expiries.setdefault(expiry_key, {}).pop(reservation_id, None)
                committed = max(int(self.committed.get(committed_key, 0)), db_total)
                self.committed[committed_key] = committed
                return [first, committed, sum(self.amounts.setdefault(amount_key, {}).values())]

            if script == ledger_module._FINALIZE_SCRIPT:
                expiry_key, amount_key, committed_key, settled_key = keys
                now = int(argv[0])
                event_id, reservation_id = str(argv[1]), str(argv[2])
                db_total = int(argv[3])
                self._cleanup(expiry_key, amount_key, now)
                markers = self.settled.setdefault(settled_key, set())
                first = int(event_id not in markers)
                if first:
                    markers.add(event_id)
                    self.amounts.setdefault(amount_key, {}).pop(reservation_id, None)
                    self.expiries.setdefault(expiry_key, {}).pop(reservation_id, None)
                committed = max(int(self.committed.get(committed_key, 0)), db_total)
                self.committed[committed_key] = committed
                return [first, committed, sum(self.amounts.setdefault(amount_key, {}).values())]

            if script == ledger_module._RELEASE_SCRIPT:
                expiry_key, amount_key = keys
                reservation_id = str(argv[0])
                self.expiries.setdefault(expiry_key, {}).pop(reservation_id, None)
                return self.amounts.setdefault(amount_key, {}).pop(reservation_id, 0)
        raise AssertionError("Unknown Lua script")


@pytest.mark.asyncio
async def test_quota_ledger_reserves_concurrently_and_settles_idempotently():
    redis = InMemoryQuotaRedis()
    ledger = TokenQuotaLedger(redis)
    user_id = uuid4()
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    window = get_billing_window(now)
    reservations = [uuid4(), uuid4(), uuid4()]

    async def reserve(reservation_id):
        return await ledger.reserve(
            user_id=user_id,
            window_start=window.start,
            now=now,
            db_committed_tokens=100_000,
            requested_tokens=200_000,
            quota_limit=500_000,
            reservation_id=reservation_id,
            expires_at=now + timedelta(hours=1),
            retention_ttl_seconds=100_000,
        )

    first, second = await asyncio.gather(reserve(reservations[0]), reserve(reservations[1]))
    rejected = await reserve(reservations[2])
    assert first.allowed and second.allowed
    assert not rejected.allowed
    assert rejected.committed_tokens == 100_000
    assert rejected.pending_tokens == 400_000

    usage_event_id = uuid4()
    settled = await ledger.settle_usage(
        user_id=user_id,
        window_start=window.start,
        now=now,
        event_id=usage_event_id,
        reservation_id=reservations[0],
        actual_tokens=75_000,
        db_committed_tokens=175_000,
        retention_ttl_seconds=100_000,
    )
    duplicate = await ledger.settle_usage(
        user_id=user_id,
        window_start=window.start,
        now=now,
        event_id=usage_event_id,
        reservation_id=reservations[0],
        actual_tokens=75_000,
        db_committed_tokens=175_000,
        retention_ttl_seconds=100_000,
    )
    assert settled.pending_tokens == 325_000
    assert duplicate.pending_tokens == 325_000

    finalized = await ledger.finalize(
        user_id=user_id,
        window_start=window.start,
        now=now,
        event_id=uuid4(),
        reservation_id=reservations[0],
        db_committed_tokens=175_000,
        retention_ttl_seconds=100_000,
    )
    assert finalized.committed_tokens == 175_000
    assert finalized.pending_tokens == 200_000


def test_billing_window_and_expired_membership_are_canonical():
    now = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
    window = get_billing_window(now)
    assert window.start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert window.end == datetime(2027, 1, 1, tzinfo=timezone.utc)
    expired = SimpleNamespace(
        is_admin=False,
        user_level=UserLevel.PREMIUM,
        membership_expires_at=now - timedelta(seconds=1),
    )
    admin = SimpleNamespace(is_admin=True, user_level=UserLevel.BASIC)
    assert get_effective_user_level(expired, now) == UserLevel.BASIC
    assert get_effective_user_level(admin, now) == UserLevel.ADMIN


def _claims(now=None):
    current = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    window = get_billing_window(current)
    return UsageContextClaims(
        reservation_id=uuid4(),
        user_id=uuid4(),
        session_id=uuid4(),
        window_start=window.start,
        window_end=window.end,
        expires_at=current + timedelta(minutes=5),
    )


def test_usage_context_is_typed_signed_and_expires():
    claims = _claims()
    token = create_usage_context(claims)
    assert decode_usage_context(token) == claims
    with pytest.raises(InvalidUsageContext):
        decode_usage_context(token + "tampered")

    expired = _claims(datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(InvalidUsageContext):
        decode_usage_context(create_usage_context(expired, issued_at=expired.window_start))


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _QueueRedis:
    def __init__(self):
        self.xadd = AsyncMock(return_value="1-0")


@pytest.mark.asyncio
async def test_stream_producer_verifies_context_and_re_signs_internal_payload():
    claims = _claims()
    event = RuntimeTokenUsageEvent(
        event_id=uuid4(),
        kind="usage",
        occurred_at=datetime.now(timezone.utc),
        model_name="model-a",
        request_type="lead",
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        usage_source="usage_metadata",
    )
    redis = _QueueRedis()
    producer = queue_module.TokenUsageStreamProducer(redis)
    envelope = RuntimeTokenUsageEnvelope(
        usage_context=create_usage_context(claims),
        event=event,
    )

    assert await producer.enqueue(envelope) == "1-0"
    _stream, fields = redis.xadd.await_args.args
    queued_payload = json.loads(fields["payload"])
    assert "usage_context" not in queued_payload
    assert queued_payload["claims"]["user_id"] == str(claims.user_id)
    assert fields["signature"] == sign_queue_payload(queued_payload)


@pytest.mark.asyncio
async def test_stream_consumer_redacts_invalid_event_reason(monkeypatch, caplog):
    marker = "private-invalid-usage-event-detail"
    redis = SimpleNamespace(
        xadd=AsyncMock(return_value="dead-1"),
        xack=AsyncMock(),
        xdel=AsyncMock(),
    )
    consumer = queue_module.TokenUsageStreamConsumer(redis, _SessionContext)
    monkeypatch.setattr(
        consumer,
        "_process_one",
        AsyncMock(side_effect=ValueError(marker)),
    )

    await consumer._process_messages([("1-0", {"payload": "{}"})])

    assert marker not in caplog.text
    _stream, dead_letter = redis.xadd.await_args.args
    assert dead_letter["reason"] == "ValueError"
    assert marker not in dead_letter["reason"]
    redis.xack.assert_awaited_once()
    redis.xdel.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_consumer_commits_db_before_settling_redis(monkeypatch):
    calls = []
    claims = _claims()
    event = RuntimeTokenUsageEvent(
        event_id=uuid4(),
        kind="usage",
        occurred_at=datetime.now(timezone.utc),
        model_name="model-a",
        request_type="lead",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        usage_source="usage_metadata",
    )
    accepted_payload = {
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "claims": claims.to_queue_dict(),
        "event": event.model_dump(mode="json"),
    }
    fields = {
        "payload": json.dumps(accepted_payload, default=str),
        "signature": sign_queue_payload(accepted_payload),
    }

    class FakeUsageService:
        def __init__(self, _session):
            pass

        async def record_runtime_event(self, **kwargs):
            calls.append("db_commit")
            return 15

    class FakeLedger:
        def __init__(self, _redis):
            pass

        async def settle_usage(self, **kwargs):
            calls.append("redis_settle")

    monkeypatch.setattr(queue_module, "TokenUsageService", FakeUsageService)
    monkeypatch.setattr(queue_module, "TokenQuotaLedger", FakeLedger)
    consumer = queue_module.TokenUsageStreamConsumer(_QueueRedis(), _SessionContext)
    await consumer._process_one(fields)
    assert calls == ["db_commit", "redis_settle"]


@pytest.mark.asyncio
async def test_finalize_waits_for_all_usage_event_ids(monkeypatch):
    claims = _claims()
    usage_event_id = uuid4()
    finalize = RuntimeTokenUsageEvent(
        event_id=uuid4(),
        kind="finalize",
        occurred_at=datetime.now(timezone.utc),
        usage_event_ids=[usage_event_id],
    )
    accepted_payload = {
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "claims": claims.to_queue_dict(),
        "event": finalize.model_dump(mode="json"),
    }
    fields = {
        "payload": json.dumps(accepted_payload, default=str),
        "signature": sign_queue_payload(accepted_payload),
    }
    committed = False
    ledger = SimpleNamespace(finalize=AsyncMock())

    class FakeUsageService:
        def __init__(self, _session):
            pass

        async def all_events_committed(self, event_ids):
            return committed

        async def get_billing_window_total(self, claims):
            return 20

    monkeypatch.setattr(queue_module, "TokenUsageService", FakeUsageService)
    monkeypatch.setattr(queue_module, "TokenQuotaLedger", lambda _redis: ledger)
    consumer = queue_module.TokenUsageStreamConsumer(_QueueRedis(), _SessionContext)
    with pytest.raises(queue_module.PendingUsageEvents):
        await consumer._process_one(fields)
    ledger.finalize.assert_not_awaited()

    committed = True
    await consumer._process_one(fields)
    ledger.finalize.assert_awaited_once()
