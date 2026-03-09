"""
RAG API 控制器
"""
import logging
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from middlewares.auth import get_current_user
from models.user import User
from exceptions import QuotaExceeded
from .schemas import ChatRequest, StreamChunk
from .service import RAGService
from .agent_client import agent_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["RAG"])


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    流式聊天接口
    
    Args:
        request: 聊天请求
        current_user: 当前用户
        db: 数据库会话
        
    Returns:
        StreamingResponse: SSE 流式响应
    """
    is_member = current_user.is_member()
    
    logger.info(f"RAG request: user={current_user.email}, session={request.session_id}, mode={request.mode}")
    
    try:
        rag_service = RAGService(db)
        
        async def generate():
            try:
                async for chunk in rag_service.chat_stream(request, current_user):
                    yield f"data: {chunk.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
            except QuotaExceeded as e:
                # 配额超限异常：返回结构化错误信息
                logger.warning(f"Quota exceeded for user {current_user.email}: {e.detail}")
                error_data = {
                    "code": "QUOTA_EXCEEDED",
                    "message": e.detail["error"]["message"],
                    "details": e.detail["error"].get("details", {})
                }
                error_chunk = StreamChunk(type="error", content=json.dumps(error_data))
                yield f"data: {error_chunk.model_dump_json()}\n\n"
            except Exception as e:
                logger.error(f"Stream error: {e}", exc_info=True)
                error_chunk = StreamChunk(type="error", content=str(e))
                yield f"data: {error_chunk.model_dump_json()}\n\n"
        
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    
    except Exception as e:
        logger.error(f"Failed to start chat stream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "service": "RAG"}


@router.post("/chat/cancel/{session_id}")
async def cancel_chat_stream(
    session_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    取消正在进行的流式聊天
    
    Args:
        session_id: 会话ID
        current_user: 当前用户
        
    Returns:
        取消结果
    """
    logger.info(f"Cancel request: user={current_user.email}, session={session_id}")
    
    try:
        result = await agent_client.cancel_generation(session_id)
        
        if result.get("success"):
            return {
                "success": True,
                "session_id": session_id,
                "message": "Cancellation signal sent"
            }
        else:
            return {
                "success": False,
                "session_id": session_id,
                "error": result.get("error", "Unknown error")
            }
    
    except Exception as e:
        logger.error(f"Failed to cancel chat stream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

