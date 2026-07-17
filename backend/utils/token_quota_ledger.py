"""Redis-backed atomic ledger for committed usage and active run reservations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


_RESERVE_SCRIPT = r"""
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, reservation_id in ipairs(expired) do
  redis.call('HDEL', KEYS[2], reservation_id)
  redis.call('ZREM', KEYS[1], reservation_id)
end

local committed = tonumber(redis.call('GET', KEYS[3]) or '0')
local db_committed = tonumber(ARGV[2])
if db_committed > committed then
  committed = db_committed
  redis.call('SET', KEYS[3], tostring(committed))
end

local pending = 0
for _, amount in ipairs(redis.call('HVALS', KEYS[2])) do
  pending = pending + tonumber(amount)
end

local requested = tonumber(ARGV[3])
local quota_limit = tonumber(ARGV[4])
if committed + pending + requested > quota_limit then
  return {0, committed, pending}
end

redis.call('HSET', KEYS[2], ARGV[5], tostring(requested))
redis.call('ZADD', KEYS[1], ARGV[6], ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[7])
redis.call('EXPIRE', KEYS[2], ARGV[7])
redis.call('EXPIRE', KEYS[3], ARGV[7])
return {1, committed, pending + requested}
"""


_SNAPSHOT_SCRIPT = r"""
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, reservation_id in ipairs(expired) do
  redis.call('HDEL', KEYS[2], reservation_id)
  redis.call('ZREM', KEYS[1], reservation_id)
end
local committed = tonumber(redis.call('GET', KEYS[3]) or '0')
local db_committed = tonumber(ARGV[2])
if db_committed > committed then
  committed = db_committed
  redis.call('SET', KEYS[3], tostring(committed))
end
local pending = 0
for _, amount in ipairs(redis.call('HVALS', KEYS[2])) do
  pending = pending + tonumber(amount)
end
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[3])
redis.call('EXPIRE', KEYS[3], ARGV[3])
return {committed, pending}
"""


_SETTLE_SCRIPT = r"""
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, reservation_id in ipairs(expired) do
  redis.call('HDEL', KEYS[2], reservation_id)
  redis.call('ZREM', KEYS[1], reservation_id)
end

local first_settlement = 0
if redis.call('SISMEMBER', KEYS[4], ARGV[2]) == 0 then
  first_settlement = 1
  local remaining = tonumber(redis.call('HGET', KEYS[2], ARGV[3]) or '0')
  local actual = tonumber(ARGV[4])
  local reduction = math.min(remaining, actual)
  local new_remaining = remaining - reduction
  if remaining > 0 and new_remaining > 0 then
    redis.call('HSET', KEYS[2], ARGV[3], tostring(new_remaining))
  elseif remaining > 0 then
    redis.call('HDEL', KEYS[2], ARGV[3])
    redis.call('ZREM', KEYS[1], ARGV[3])
  end
  redis.call('SADD', KEYS[4], ARGV[2])
end

local committed = tonumber(redis.call('GET', KEYS[3]) or '0')
local db_committed = tonumber(ARGV[5])
if db_committed > committed then
  committed = db_committed
  redis.call('SET', KEYS[3], tostring(committed))
end
local pending = 0
for _, amount in ipairs(redis.call('HVALS', KEYS[2])) do
  pending = pending + tonumber(amount)
end
for _, key in ipairs(KEYS) do
  redis.call('EXPIRE', key, ARGV[6])
end
return {first_settlement, committed, pending}
"""


_FINALIZE_SCRIPT = r"""
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, reservation_id in ipairs(expired) do
  redis.call('HDEL', KEYS[2], reservation_id)
  redis.call('ZREM', KEYS[1], reservation_id)
end

local first_finalization = 0
if redis.call('SISMEMBER', KEYS[4], ARGV[2]) == 0 then
  first_finalization = 1
  redis.call('HDEL', KEYS[2], ARGV[3])
  redis.call('ZREM', KEYS[1], ARGV[3])
  redis.call('SADD', KEYS[4], ARGV[2])
end
local committed = tonumber(redis.call('GET', KEYS[3]) or '0')
local db_committed = tonumber(ARGV[4])
if db_committed > committed then
  committed = db_committed
  redis.call('SET', KEYS[3], tostring(committed))
end
local pending = 0
for _, amount in ipairs(redis.call('HVALS', KEYS[2])) do
  pending = pending + tonumber(amount)
end
for _, key in ipairs(KEYS) do
  redis.call('EXPIRE', key, ARGV[5])
end
return {first_finalization, committed, pending}
"""


_RELEASE_SCRIPT = r"""
local released = tonumber(redis.call('HGET', KEYS[2], ARGV[1]) or '0')
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[1], ARGV[1])
return released
"""


@dataclass(frozen=True, slots=True)
class QuotaLedgerSnapshot:
    committed_tokens: int
    pending_tokens: int


@dataclass(frozen=True, slots=True)
class QuotaReservationResult(QuotaLedgerSnapshot):
    allowed: bool


class TokenQuotaLedger:
    """Own all Redis key and Lua semantics for one user's billing-window state."""

    def __init__(self, redis_client):
        self.redis = redis_client

    @staticmethod
    def _keys(user_id: UUID, window_start: datetime) -> tuple[str, str, str, str]:
        slot = f"{user_id}:{window_start:%Y%m}"
        prefix = f"token_quota:v2:{{{slot}}}"
        return (
            f"{prefix}:active-expiry",
            f"{prefix}:active-amount",
            f"{prefix}:committed",
            f"{prefix}:settled-events",
        )

    async def reserve(
        self,
        *,
        user_id: UUID,
        window_start: datetime,
        now: datetime,
        db_committed_tokens: int,
        requested_tokens: int,
        quota_limit: int,
        reservation_id: UUID,
        expires_at: datetime,
        retention_ttl_seconds: int,
    ) -> QuotaReservationResult:
        keys = self._keys(user_id, window_start)
        result = await self.redis.eval(
            _RESERVE_SCRIPT,
            3,
            *keys[:3],
            int(now.timestamp()),
            int(db_committed_tokens),
            int(requested_tokens),
            int(quota_limit),
            str(reservation_id),
            int(expires_at.timestamp()),
            int(retention_ttl_seconds),
        )
        return QuotaReservationResult(
            allowed=bool(int(result[0])),
            committed_tokens=int(result[1]),
            pending_tokens=int(result[2]),
        )

    async def snapshot(
        self,
        *,
        user_id: UUID,
        window_start: datetime,
        now: datetime,
        db_committed_tokens: int,
        retention_ttl_seconds: int,
    ) -> QuotaLedgerSnapshot:
        keys = self._keys(user_id, window_start)
        result = await self.redis.eval(
            _SNAPSHOT_SCRIPT,
            3,
            *keys[:3],
            int(now.timestamp()),
            int(db_committed_tokens),
            int(retention_ttl_seconds),
        )
        return QuotaLedgerSnapshot(int(result[0]), int(result[1]))

    async def settle_usage(
        self,
        *,
        user_id: UUID,
        window_start: datetime,
        now: datetime,
        event_id: UUID,
        reservation_id: UUID,
        actual_tokens: int,
        db_committed_tokens: int,
        retention_ttl_seconds: int,
    ) -> QuotaLedgerSnapshot:
        keys = self._keys(user_id, window_start)
        result = await self.redis.eval(
            _SETTLE_SCRIPT,
            4,
            *keys,
            int(now.timestamp()),
            str(event_id),
            str(reservation_id),
            int(actual_tokens),
            int(db_committed_tokens),
            int(retention_ttl_seconds),
        )
        return QuotaLedgerSnapshot(int(result[1]), int(result[2]))

    async def finalize(
        self,
        *,
        user_id: UUID,
        window_start: datetime,
        now: datetime,
        event_id: UUID,
        reservation_id: UUID,
        db_committed_tokens: int,
        retention_ttl_seconds: int,
    ) -> QuotaLedgerSnapshot:
        keys = self._keys(user_id, window_start)
        result = await self.redis.eval(
            _FINALIZE_SCRIPT,
            4,
            *keys,
            int(now.timestamp()),
            str(event_id),
            str(reservation_id),
            int(db_committed_tokens),
            int(retention_ttl_seconds),
        )
        return QuotaLedgerSnapshot(int(result[1]), int(result[2]))

    async def release_reservation(
        self,
        *,
        user_id: UUID,
        window_start: datetime,
        reservation_id: UUID,
    ) -> int:
        keys = self._keys(user_id, window_start)
        return int(
            await self.redis.eval(
                _RELEASE_SCRIPT,
                2,
                *keys[:2],
                str(reservation_id),
            )
        )
