"""
Async queue for token usage writes using Redis.

Decouples token usage recording from request handling to improve
response latency and handle high concurrency (300+).
"""
import asyncio
import json
import logging
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Redis key prefix for token usage queue
QUEUE_KEY = "token_usage:queue:pending"
PROCESSING_KEY = "token_usage:queue:processing"
BATCH_SIZE = 50
FLUSH_INTERVAL = 2.0  # seconds


class TokenUsageQueueProducer:
    """Producer: pushes token usage data to Redis queue."""
    
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def push(
        self,
        user_id: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        session_id: Optional[str] = None,
        request_type: Optional[str] = None
    ) -> bool:
        """
        Push token usage data to queue.
        
        Returns True if successful, False otherwise.
        """
        try:
            data = {
                "user_id": user_id,
                "model_name": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "session_id": session_id,
                "request_type": request_type,
                "queued_at": datetime.now(timezone.utc).isoformat()
            }
            await self.redis.lpush(QUEUE_KEY, json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"Failed to push token usage to queue: {e}")
            return False


class TokenUsageQueueConsumer:
    """Consumer: processes token usage queue and batch writes to DB."""
    
    def __init__(self, redis_client, session_factory):
        self.redis = redis_client
        self.session_factory = session_factory
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the background consumer."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("Token usage queue consumer started")
    
    async def stop(self):
        """Stop the background consumer gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Process remaining items before shutdown
        await self._flush_remaining()
        logger.info("Token usage queue consumer stopped")
    
    async def _consume_loop(self):
        """Main consumer loop."""
        while self._running:
            try:
                await self._process_batch()
                await asyncio.sleep(FLUSH_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in token usage consumer loop: {e}")
                await asyncio.sleep(FLUSH_INTERVAL)
    
    async def _process_batch(self):
        """Process a batch of items from the queue."""
        items = []
        try:
            # Pop up to BATCH_SIZE items
            for _ in range(BATCH_SIZE):
                item = await self.redis.rpop(QUEUE_KEY)
                if item is None:
                    break
                items.append(item)
            
            if not items:
                return
            
            # Process each item with a fresh session
            from services.token_usage_service import TokenUsageService
            
            success_count = 0
            async with self.session_factory() as session:
                service = TokenUsageService(session)
                for item in items:
                    try:
                        data = json.loads(item)
                        result = await service.record_usage(
                            user_id=data["user_id"],
                            model_name=data["model_name"],
                            input_tokens=data["input_tokens"],
                            output_tokens=data["output_tokens"],
                            session_id=data.get("session_id"),
                            request_type=data.get("request_type")
                        )
                        if result:
                            success_count += 1
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON in queue item: {e}")
                    except Exception as e:
                        logger.error(f"Failed to process queue item: {e}")
            
            if items:
                logger.debug(
                    f"Processed {success_count}/{len(items)} token usage records"
                )
        except Exception as e:
            logger.error(f"Error processing token usage batch: {e}")
            # Re-queue failed items
            for item in items:
                try:
                    await self.redis.lpush(QUEUE_KEY, item)
                except Exception:
                    pass
    
    async def _flush_remaining(self):
        """Flush remaining items on shutdown."""
        try:
            remaining = await self.redis.llen(QUEUE_KEY)
            if remaining > 0:
                logger.info(f"Flushing {remaining} remaining token usage records")
                from services.token_usage_service import TokenUsageService
                
                async with self.session_factory() as session:
                    service = TokenUsageService(session)
                    while True:
                        item = await self.redis.rpop(QUEUE_KEY)
                        if item is None:
                            break
                        try:
                            data = json.loads(item)
                            await service.record_usage(
                                user_id=data["user_id"],
                                model_name=data["model_name"],
                                input_tokens=data["input_tokens"],
                                output_tokens=data["output_tokens"],
                                session_id=data.get("session_id"),
                                request_type=data.get("request_type")
                            )
                        except Exception as e:
                            logger.error(f"Failed to flush queue item: {e}")
        except Exception as e:
            logger.error(f"Error flushing remaining items: {e}")


# Global instances
_producer: Optional[TokenUsageQueueProducer] = None
_consumer: Optional[TokenUsageQueueConsumer] = None


async def init_token_usage_queue(redis_client, session_factory):
    """
    Initialize the token usage queue system.
    
    Args:
        redis_client: Redis client instance
        session_factory: Async session factory for database access
    """
    global _producer, _consumer
    
    _producer = TokenUsageQueueProducer(redis_client)
    _consumer = TokenUsageQueueConsumer(redis_client, session_factory)
    await _consumer.start()
    
    logger.info("Token usage queue initialized")


async def shutdown_token_usage_queue():
    """Shutdown the token usage queue system."""
    global _consumer
    if _consumer:
        await _consumer.stop()
        _consumer = None
    logger.info("Token usage queue shutdown complete")


def get_producer() -> Optional[TokenUsageQueueProducer]:
    """Get the global producer instance."""
    return _producer
