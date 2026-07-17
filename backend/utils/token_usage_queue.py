"""Reliable Redis Stream transport for trusted Runtime token usage events."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import socket
import uuid

from pydantic import ValidationError
from redis.exceptions import ResponseError

from config.settings import settings
from schemas.token_usage import RuntimeTokenUsageEnvelope, RuntimeTokenUsageEvent
from services.token_usage_service import TokenUsageService
from utils.token_quota_ledger import TokenQuotaLedger
from utils.token_usage_context import (
    InvalidUsageContext,
    UsageContextClaims,
    decode_usage_context,
    sign_queue_payload,
    verify_queue_payload,
)


logger = logging.getLogger(__name__)

STREAM_KEY = "token_usage:events:v2"
DEAD_LETTER_STREAM_KEY = "token_usage:events:v2:dead-letter"
CONSUMER_GROUP = "token_usage_db_v2"
BATCH_SIZE = 50


class PendingUsageEvents(RuntimeError):
    """A finalize marker arrived before all usage events committed."""


def _retention_ttl(claims: UsageContextClaims, now: datetime) -> int:
    until_window_cleanup = int((claims.window_end - now).total_seconds()) + 24 * 60 * 60
    return max(
        until_window_cleanup,
        int(settings.TOKEN_QUOTA_RESERVATION_TTL_SECONDS) + 24 * 60 * 60,
        60,
    )


class TokenUsageStreamProducer:
    """Accept a verified Runtime envelope into the durable internal stream."""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def enqueue(self, envelope: RuntimeTokenUsageEnvelope) -> str:
        claims = decode_usage_context(envelope.usage_context)
        accepted_payload = {
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "claims": claims.to_queue_dict(),
            "event": envelope.event.model_dump(mode="json"),
        }
        signature = sign_queue_payload(accepted_payload)
        return await self.redis.xadd(
            STREAM_KEY,
            {
                "payload": json.dumps(
                    accepted_payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "signature": signature,
            },
        )


class TokenUsageStreamConsumer:
    """Persist, settle, and acknowledge events safely across API replicas."""

    def __init__(self, redis_client, session_factory):
        self.redis = redis_client
        self.session_factory = session_factory
        self.consumer_name = (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        try:
            await self.redis.xgroup_create(
                STREAM_KEY,
                CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._running = True
        self._task = asyncio.create_task(
            self._consume_loop(),
            name=f"token-usage-consumer-{self.consumer_name}",
        )
        logger.info("Token usage stream consumer started as %s", self.consumer_name)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Token usage stream consumer stopped")

    async def _consume_loop(self) -> None:
        while self._running:
            try:
                claimed = await self._claim_stale()
                if claimed:
                    await self._process_messages(claimed)

                batches = await self.redis.xreadgroup(
                    CONSUMER_GROUP,
                    self.consumer_name,
                    streams={STREAM_KEY: ">"},
                    count=BATCH_SIZE,
                    block=int(settings.TOKEN_USAGE_STREAM_BLOCK_MILLISECONDS),
                )
                for _stream_name, messages in batches or []:
                    await self._process_messages(messages)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Token usage stream consumer loop failed (error_type=%s)",
                    type(exc).__name__,
                )
                await asyncio.sleep(1)

    async def _claim_stale(self) -> list[tuple[str, dict[str, str]]]:
        result = await self.redis.xautoclaim(
            STREAM_KEY,
            CONSUMER_GROUP,
            self.consumer_name,
            min_idle_time=int(settings.TOKEN_USAGE_STREAM_CLAIM_IDLE_SECONDS) * 1000,
            start_id="0-0",
            count=BATCH_SIZE,
        )
        if not result or len(result) < 2:
            return []
        return list(result[1] or [])

    async def _process_messages(
        self,
        messages: list[tuple[str, dict[str, str]]],
    ) -> None:
        for stream_id, fields in messages:
            try:
                await self._process_one(fields)
            except (json.JSONDecodeError, ValidationError, InvalidUsageContext, ValueError) as exc:
                reason = type(exc).__name__
                logger.warning(
                    "Discarding permanently invalid token usage event %s "
                    "(error_type=%s)",
                    stream_id,
                    reason,
                )
                await self._dead_letter(stream_id, fields, reason)
            except PendingUsageEvents:
                logger.debug(
                    "Deferring token usage finalization %s until prior events commit",
                    stream_id,
                )
                continue
            except Exception as exc:
                logger.error(
                    "Transient failure processing token usage event %s; "
                    "leaving pending (error_type=%s)",
                    stream_id,
                    type(exc).__name__,
                )
                continue
            await self.redis.xack(STREAM_KEY, CONSUMER_GROUP, stream_id)
            await self.redis.xdel(STREAM_KEY, stream_id)

    async def _dead_letter(
        self,
        stream_id: str,
        fields: dict[str, str],
        reason: str,
    ) -> None:
        await self.redis.xadd(
            DEAD_LETTER_STREAM_KEY,
            {
                "source_id": stream_id,
                "reason": reason[:500],
                "payload": str(fields.get("payload") or "")[:200_000],
            },
        )

    async def _process_one(self, fields: dict[str, str]) -> None:
        payload = json.loads(fields.get("payload") or "")
        if not isinstance(payload, dict):
            raise ValueError("Queue payload must be an object")
        if not verify_queue_payload(payload, fields.get("signature") or ""):
            raise InvalidUsageContext("Queue payload signature mismatch")

        claims_payload = payload.get("claims")
        if not isinstance(claims_payload, dict):
            raise InvalidUsageContext("Queued claims must be an object")
        claims = UsageContextClaims.from_queue_dict(claims_payload)
        event = RuntimeTokenUsageEvent.model_validate(payload.get("event"))
        now = datetime.now(timezone.utc)
        retention_ttl = _retention_ttl(claims, now)

        async with self.session_factory() as session:
            service = TokenUsageService(session)
            ledger = TokenQuotaLedger(self.redis)
            if event.kind == "usage":
                db_total = await service.record_runtime_event(claims=claims, event=event)
                assert event.total_tokens is not None
                await ledger.settle_usage(
                    user_id=claims.user_id,
                    window_start=claims.window_start,
                    now=now,
                    event_id=event.event_id,
                    reservation_id=claims.reservation_id,
                    actual_tokens=event.total_tokens,
                    db_committed_tokens=db_total,
                    retention_ttl_seconds=retention_ttl,
                )
                return

            usage_event_ids = list(event.usage_event_ids)
            if not await service.all_events_committed(usage_event_ids):
                raise PendingUsageEvents()
            db_total = await service.get_billing_window_total(claims)
            await ledger.finalize(
                user_id=claims.user_id,
                window_start=claims.window_start,
                now=now,
                event_id=event.event_id,
                reservation_id=claims.reservation_id,
                db_committed_tokens=db_total,
                retention_ttl_seconds=retention_ttl,
            )


_producer: TokenUsageStreamProducer | None = None
_consumer: TokenUsageStreamConsumer | None = None


async def init_token_usage_queue(redis_client, session_factory) -> None:
    global _producer, _consumer
    _producer = TokenUsageStreamProducer(redis_client)
    _consumer = TokenUsageStreamConsumer(redis_client, session_factory)
    await _consumer.start()


async def shutdown_token_usage_queue() -> None:
    global _producer, _consumer
    if _consumer is not None:
        await _consumer.stop()
    _consumer = None
    _producer = None


def get_token_usage_producer() -> TokenUsageStreamProducer | None:
    return _producer
