"""FastAPI application for the agent system."""
import sys
import re
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
import json
import asyncio

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.agent.agent import create_agent
from src.utils.logger import setup_logger
from src.utils.cancellation_manager import get_cancellation_manager
from src.mcp import MCPClientManager
from src.mcp.tool_adapter import create_mcp_tools
from src.tools.registry import get_tool_registry
from src.skills.loader import get_skill_loader
from src.utils.minio_client import get_agent_minio_client
from src.utils.workspace_manager import get_workspace_manager
from config import get_settings

# Initialize settings and logger
settings = get_settings()

# Setup root logger so all modules can output logs
root_logger = setup_logger(
    "",  # Empty string = root logger
    log_level=settings.log_level,
    log_file=settings.log_file
)

# Also setup named logger for this module
logger = logging.getLogger("agent_api")
logger.setLevel(settings.log_level)

# Create FastAPI app
app = FastAPI(
    title="Intelligent Agent API",
    description="Production-grade intelligent agent for task processing",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global agent instance
agent = None
mcp_client_manager = None

# Path safety regex: only allow alphanumeric, underscore, hyphen, and dot
_SAFE_PATH_RE = re.compile(r'^[a-zA-Z0-9_\-\.]+$')


def _validate_path_param(value: str) -> bool:
    """Validate that a path parameter is safe (no traversal, no special chars)."""
    if not value or '..' in value or '/' in value or '\\' in value:
        return False
    return bool(_SAFE_PATH_RE.match(value))


async def periodic_workspace_cleanup():
    """每 30 分钟清理一次过期 Workspace 和检查磁盘用量。"""
    workspace_manager = get_workspace_manager()
    while True:
        await asyncio.sleep(1800)
        try:
            removed = workspace_manager.cleanup_stale()
            usage_gb = workspace_manager.check_disk_usage()
            logger.info(
                f"Workspace cleanup: removed {removed} stale dirs, "
                f"disk usage: {usage_gb:.2f} GB"
            )
        except Exception as e:
            logger.warning(f"Workspace cleanup error: {e}")


# Request/Response models
class QueryRequest(BaseModel):
    """Request model for query processing."""
    
    user_query: str = Field(
        ...,
        description="The user's question or request",
        min_length=1,
        max_length=10000
    )
    enable_web_search: Optional[bool] = Field(
        None,
        description="Optional override for web search enablement"
    )
    show_thinking: Optional[bool] = Field(
        True,
        description="Whether to show thinking process in streaming response (default: True)"
    )
    session_id: Optional[str] = Field(
        None,
        description="Optional session ID for multi-turn conversation (auto-loads history if exists)"
    )
    
    # Direct content mode (for small documents)
    content: Optional[str] = Field(
        None,
        description="Full document content. If provided and small enough, will be used directly instead of recall"
    )
    
    # Multi-document content mode (for multi-doc summary)
    document_contents: Optional[dict] = Field(
        None,
        description="Multiple documents' content. Dict[doc_id, markdown_content]. Used for multi-document summary mode."
    )
    
    # Document names mapping (for better display in prompts)
    document_names: Optional[dict] = Field(
        None,
        description="Document names mapping. Dict[doc_id, doc_name]. If not provided, doc_id will be used as fallback."
    )
    
    # Knowledge base and user info (for internal document loading)
    kb_id: Optional[str] = Field(
        None,
        description="Knowledge base ID (for Agent to load documents internally)"
    )
    user_id: Optional[str] = Field(
        None,
        description="User ID (for Agent to load documents internally)"
    )
    
    # Dynamic configuration (required)
    openai_api_key: str = Field(
        ...,
        description="Model API key (required)"
    )
    openai_api_base: str = Field(
        ...,
        description="Model API base URL (required, e.g., http://localhost:7997/v1)"
    )
    model_name: str = Field(
        ...,
        description="Model name (required, e.g., Qwen3-30B-A3B-Instruct-2507)"
    )
    max_context_tokens: int = Field(
        ...,
        description="Maximum context tokens (required)",
        gt=0
    )
    search_engine: Optional[str] = Field(
        "tavily",
        description="Search engine to use: 'tavily' or 'bocha' (default: 'tavily')"
    )
    search_engine_api_key: Optional[str] = Field(
        None,
        description="Search engine API key for web search (optional)"
    )
    
    # Recall API configuration (required)
    recall_api_url: str = Field(
        ...,
        description="Recall API URL (required)"
    )
    recall_index_names: str = Field(
        ...,
        description="Recall index names (required, comma-separated)"
    )
    recall_doc_ids: str = Field(
        "",
        description="Recall document IDs (optional, comma-separated or JSON array)"
    )
    recall_es_host: str = Field(
        ...,
        description="Elasticsearch host (required)"
    )
    recall_top_n: int = Field(
        ...,
        description="Recall top N results (required)",
        gt=0
    )
    recall_similarity_threshold: float = Field(
        ...,
        description="Recall similarity threshold (required)",
        ge=0.0,
        le=1.0
    )
    recall_vector_similarity_weight: float = Field(
        ...,
        description="Recall vector similarity weight (required)",
        ge=0.0,
        le=1.0
    )
    
    # Recall Model configuration (required)
    recall_model_factory: str = Field(
        ...,
        description="Recall model factory (required, e.g., VLLM, HuggingFace)"
    )
    recall_model_name: str = Field(
        ...,
        description="Recall model name (required, e.g., bge-m3)"
    )
    recall_model_base_url: str = Field(
        ...,
        description="Recall model base URL (required)"
    )
    recall_api_key: str = Field(
        ...,
        description="Recall API key (required)"
    )
    
    # Recall Rerank configuration (required)
    recall_use_rerank: bool = Field(
        ...,
        description="Enable/disable rerank (required)"
    )
    recall_rerank_factory: str = Field(
        "",
        description="Recall rerank factory (required if rerank enabled)"
    )
    recall_rerank_model_name: str = Field(
        "",
        description="Recall rerank model name (required if rerank enabled, e.g., bge-reranker-v2-m3)"
    )
    recall_rerank_base_url: str = Field(
        "",
        description="Recall rerank base URL (required if rerank enabled)"
    )
    recall_rerank_api_key: str = Field(
        "",
        description="Recall rerank API key (required if rerank enabled)"
    )
    
    @model_validator(mode='after')
    def validate_rerank_config(self):
        """Validate that if rerank is enabled, all rerank parameters are provided."""
        if self.recall_use_rerank:
            missing = []
            if not self.recall_rerank_factory:
                missing.append("recall_rerank_factory")
            if not self.recall_rerank_model_name:
                missing.append("recall_rerank_model_name")
            if not self.recall_rerank_base_url:
                missing.append("recall_rerank_base_url")
            if not self.recall_rerank_api_key:
                missing.append("recall_rerank_api_key")
            
            if missing:
                raise ValueError(
                    f"When recall_use_rerank is True, the following parameters are required: {', '.join(missing)}"
                )
        return self


@app.on_event("startup")
async def startup_event():
    """Initialize the agent and MCP client manager on startup."""
    global agent, mcp_client_manager
    logger.info("=" * 70)
    logger.info("🚀 Starting Intelligent Agent API (Dynamic Configuration Mode)")
    logger.info("=" * 70)
    
    try:
        # Agent initialization with minimal dependencies
        # LLM, Recall, and Web Search tools will be created per-request
        agent = create_agent()
        logger.info("✅ Agent initialized successfully")
        
        # Initialize Skills (Anthropic Agent Skills compatible)
        skill_loader = get_skill_loader()
        skill_count = len(skill_loader.skills)
        if skill_count > 0:
            logger.info(f"✅ Skills loaded: {skill_count} skill(s)")
            for skill in skill_loader.get_all_skills():
                logger.info(f"   - {skill.name}: {skill.description[:60]}...")
        else:
            logger.info("📌 No skills installed (place skill directories in agent/skills/)")
        
        # Initialize MCP Client Manager
        config_path = Path(__file__).parent / "config" / "mcp_servers.json"
        mcp_client_manager = MCPClientManager(str(config_path))
        await mcp_client_manager.initialize()
        
        connected_servers = mcp_client_manager.get_connected_servers()
        available_tools = mcp_client_manager.get_available_tools()
        
        if connected_servers:
            logger.info(f"✅ MCP initialized: {len(connected_servers)} server(s) connected")
            logger.info(f"📌 MCP servers: {', '.join(connected_servers)}")
            logger.info(f"📌 MCP tools available: {len(available_tools)}")
            for tool in available_tools:
                logger.info(f"   - {tool.name}: {tool.description[:60]}...")
            
            # Register MCP tools to ToolRegistry for ReAct agent
            mcp_tool_adapters = create_mcp_tools(mcp_client_manager)
            tool_registry = get_tool_registry()
            for adapter in mcp_tool_adapters:
                # Get server_id from the tool
                server_id = mcp_client_manager._tool_to_server.get(adapter.name, "unknown")
                tool_registry.register_mcp_tools([adapter], server_id)
            logger.info(f"✅ Registered {len(mcp_tool_adapters)} MCP tools to ToolRegistry")
        else:
            logger.warning("⚠️ No MCP servers connected (MCP features disabled)")
        
        logger.info("📌 Configuration model: All LLM and Recall parameters required in request body")
        logger.info("📌 Session management: PostgreSQL + Redis")
        
        # Start periodic workspace cleanup task
        asyncio.create_task(periodic_workspace_cleanup())
        logger.info("✅ Periodic workspace cleanup task started (every 1800s)")
        
        logger.info("=" * 70)
    except Exception as e:
        logger.error(f"❌ Failed to initialize agent: {str(e)}", exc_info=True)
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    global mcp_client_manager
    logger.info("Shutting down agent API...")
    
    # Cleanup MCP connections
    if mcp_client_manager:
        logger.info("Disconnecting MCP servers...")
        await mcp_client_manager.disconnect_all()
        logger.info("✅ MCP servers disconnected")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Intelligent Agent API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    # MCP health info
    mcp_status = {}
    if mcp_client_manager:
        connected_servers = mcp_client_manager.get_connected_servers()
        available_tools = [t.name for t in mcp_client_manager.get_available_tools()]
        pool_stats = mcp_client_manager.get_pool_stats()
        mcp_status = {
            "enabled": True,
            "connected_servers": connected_servers,
            "available_tools": available_tools,
            "pool_stats": pool_stats
        }
    else:
        mcp_status = {
            "enabled": False,
            "connected_servers": [],
            "available_tools": [],
            "pool_stats": {}
        }
    
    return {
        "status": "healthy",
        "agent_ready": True,
        "mcp": mcp_status
    }


@app.get("/files/{session_id}/{filename}")
async def get_file_url(session_id: str, filename: str):
    """返回 MinIO 中生成文件的下载链接。

    对 session_id 和 filename 进行路径安全校验，
    然后通过 AgentMinioClient 生成 presigned URL。
    """
    if not _validate_path_param(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    if not _validate_path_param(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        client = get_agent_minio_client()
        object_name = f"agent-outputs/{session_id}/{filename}"
        url = client.get_presigned_url(object_name)
        return {"download_url": url}
    except Exception as e:
        err_msg = str(e).lower()
        if "nosuchkey" in err_msg or "not found" in err_msg or "does not exist" in err_msg:
            raise HTTPException(status_code=404, detail="File not found")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/stream")
async def process_query_stream(request: QueryRequest):
    """
    Process a user query with Server-Sent Events (SSE) streaming.
    
    The response uses SSE format with different event types:
    - thinking_start/thinking_end: Thinking process markers
    - thought_chunk: Chunk of thinking content
    - answer_chunk: Chunk of answer content
    - final_answer: Complete answer with session_id
    - token_usage: Token usage statistics
    - error: Error information
    """
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    logger.info(f"Received streaming query [session: {request.session_id or 'new'}]: {request.user_query[:100]}...")
    logger.debug(f"KB ID: {request.kb_id}, User ID: {request.user_id}")
    
    async def event_generator():
        """Generate SSE events from the agent stream."""
        cancellation_manager = get_cancellation_manager()
        session_id = request.session_id
        content_length = 0
        current_phase = "init"
        
        try:
            # Build kwargs dict for agent (same as regular endpoint)
            kwargs = {
                "user_query": request.user_query,
                "enable_web_search": request.enable_web_search,
                "session_id": request.session_id,
                "content": request.content,
                "document_contents": request.document_contents,
                "document_names": request.document_names,
                "kb_id": request.kb_id,
                "user_id": request.user_id,
                "openai_api_key": request.openai_api_key,
                "openai_api_base": request.openai_api_base,
                "model_name": request.model_name,
                "max_context_tokens": request.max_context_tokens,
                "search_engine": request.search_engine,
                "search_engine_api_key": request.search_engine_api_key,
                "recall_api_url": request.recall_api_url,
                "recall_index_names": request.recall_index_names,
                "recall_doc_ids": request.recall_doc_ids,
                "recall_es_host": request.recall_es_host,
                "recall_top_n": request.recall_top_n,
                "recall_similarity_threshold": request.recall_similarity_threshold,
                "recall_vector_similarity_weight": request.recall_vector_similarity_weight,
                "recall_model_factory": request.recall_model_factory,
                "recall_model_name": request.recall_model_name,
                "recall_model_base_url": request.recall_model_base_url,
                "recall_api_key": request.recall_api_key,
                "recall_use_rerank": request.recall_use_rerank,
                "recall_rerank_factory": request.recall_rerank_factory,
                "recall_rerank_model_name": request.recall_rerank_model_name,
                "recall_rerank_base_url": request.recall_rerank_base_url,
                "recall_rerank_api_key": request.recall_rerank_api_key
            }

            # Stream events from agent (根据请求参数决定是否显示思考过程)
            async for event in agent.process_query_stream(
                show_thinking=request.show_thinking if request.show_thinking is not None else True,
                **kwargs
            ):
                # Check for cancellation before processing each event
                if session_id and cancellation_manager.is_cancelled(session_id):
                    logger.info(f"Session {session_id} cancelled, stopping stream at phase={current_phase}, content_length={content_length}")
                    # Yield cancellation event
                    cancel_data = json.dumps({
                        "message": "Generation cancelled by user",
                        "phase": current_phase,
                        "content_length": content_length
                    }, ensure_ascii=False)
                    yield f"event: cancelled\ndata: {cancel_data}\n\n"
                    # Clear the cancellation flag
                    cancellation_manager.clear(session_id)
                    return
                
                event_type = event["type"]
                event_data = event["data"]
                
                # Track current phase and content length
                if event_type == "thinking_start":
                    current_phase = "thinking"
                elif event_type == "answer_chunk":
                    current_phase = "answer"
                    content_length += len(event_data.get("content", ""))
                elif event_type == "thought_chunk":
                    content_length += len(event_data.get("content", ""))
                
                # Format as SSE
                sse_data = json.dumps(event_data, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {sse_data}\n\n"
                
                # Small delay to avoid overwhelming the client
                await asyncio.sleep(0.01)
            
        except Exception as e:
            logger.error(f"Error in streaming query: {str(e)}", exc_info=True)
            error_data = json.dumps({"message": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"
        finally:
            # Clean up cancellation flag if it exists
            if session_id:
                cancellation_manager.clear(session_id)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Access-Control-Allow-Origin": "*"
        }
    )


@app.get("/conversation/{session_id}")
async def get_conversation_history(session_id: str):
    """
    Get the conversation history for a session.
    
    Args:
        session_id: Session ID to retrieve history for
        
    Returns:
        List of messages in the conversation
    """
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    try:
        history = agent.get_conversation_history(session_id)
        return {
            "session_id": session_id,
            "message_count": len(history),
            "messages": history
        }
    except Exception as e:
        logger.error(f"Error getting conversation history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cancel/{session_id}")
async def cancel_generation(session_id: str):
    """
    Cancel an ongoing generation for a session.
    
    This endpoint marks the session as cancelled, which will cause the
    streaming endpoint to stop generating new content and return a
    cancellation event.
    
    Args:
        session_id: Session ID to cancel
        
    Returns:
        Success status and cancellation details
    """
    try:
        cancellation_manager = get_cancellation_manager()
        cancellation_manager.cancel(session_id)
        
        logger.info(f"Cancellation requested for session: {session_id}")
        
        return {
            "success": True,
            "session_id": session_id,
            "message": "Cancellation signal sent"
        }
    except Exception as e:
        logger.error(f"Error cancelling session {session_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=settings.log_level.lower()
    )
