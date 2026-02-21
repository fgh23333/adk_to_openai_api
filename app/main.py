import logging
import base64
import uuid
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
import httpx
from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File, Header
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, Any, Optional, Annotated

from app.config import settings
from app.models import (
    ChatCompletionRequest, ChatCompletionResponse, ListModelsResponse,
    HealthResponse, ErrorResponse
)
from app.adk_client import ADKClient
from app.auth import verify_api_key_dependency

# Configure logging with request ID support
class RequestIdFilter(logging.Filter):
    """Add request_id to log records"""
    def filter(self, record):
        record.request_id = get_request_id() or '-'
        return True

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format='%(asctime)s [%(request_id)s] %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger().addFilter(RequestIdFilter())
logger = logging.getLogger(__name__)

# Context variable for request ID
request_id_var: ContextVar[str] = ContextVar('request_id', default='')

def get_request_id() -> Optional[str]:
    return request_id_var.get() or None

def set_request_id(request_id: str):
    request_id_var.set(request_id)


# Request tracking middleware
class RequestTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Generate unique request ID
        request_id = request.headers.get('X-Request-ID') or f"req_{uuid.uuid4().hex[:12]}"
        set_request_id(request_id)

        # Track timing
        start_time = time.time()

        # Add request ID to response headers
        response = await call_next(request)

        process_time = time.time() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.3f}s"

        logger.info(f"Request completed in {process_time:.3f}s")
        return response


# Initialize ADK client
adk_client = ADKClient()

# Initialize database
from app.database import init_database, get_database
db = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    # Startup
    logger.info("ADK Middleware starting up...")

    # Initialize database
    if settings.session_history_enabled:
        db = init_database(settings.database_path)
        logger.info(f"Session history database initialized: {settings.database_path}")

    yield

    # Shutdown - close HTTP client
    logger.info("ADK Middleware shutting down...")
    await adk_client.close()


# OpenAPI examples
openapi_examples = {
    "simple_text": {
        "summary": "简单文本对话",
        "description": "发送简单的文本消息",
        "value": {
            "model": "agent",
            "messages": [{"role": "user", "content": "你好，请介绍一下你自己"}],
            "stream": False
        }
    },
    "with_image": {
        "summary": "带图片的消息",
        "description": "发送图片请求分析",
        "value": {
            "model": "agent",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请描述这张图片"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                ]
            }],
            "stream": True
        }
    },
    "with_audio": {
        "summary": "带音频的消息",
        "description": "发送音频请求转录或分析",
        "value": {
            "model": "agent",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "这段音频说了什么？"},
                    {"type": "audio_url", "audio_url": {"url": "https://example.com/audio.mp3"}}
                ]
            }]
        }
    },
    "with_document": {
        "summary": "带文档的消息",
        "description": "发送文档请求分析（自动提取文本）",
        "value": {
            "model": "agent",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请总结这个文档的内容"},
                    {"type": "file", "file": {"url": "https://example.com/document.docx"}}
                ]
            }]
        }
    },
    "multi_modal": {
        "summary": "多模态消息",
        "description": "同时发送多个模态的内容",
        "value": {
            "model": "agent",
            "user": "session_123",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "对比这些内容"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image1.jpg"}},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image2.jpg"}}
                ]
            }],
            "stream": True
        }
    }
}

# Security scheme for Swagger UI
security_scheme = {
    "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "API Key 认证，格式: Bearer sk-your-api-key"
    }
}

# Create FastAPI app
app = FastAPI(
    title="ADK Middleware API",
    description="""
## 概述

将 Google ADK Agent 转换为 OpenAI 兼容的 Chat Completions API。

## 功能特性

- ✅ **多模态支持**: 图片、视频、音频、PDF、Office文档
- ✅ **流式响应**: 真正的 SSE 流式输出
- ✅ **多会话管理**: 通过 X-Session-ID 区分会话
- ✅ **并发优化**: URL 并发下载、连接池复用

## 认证

如果启用了 API Key 认证，请点击右上角 🔓 **Authorize** 按钮输入 API Key。

格式: `Bearer sk-your-api-key`

## 多会话支持

通过以下方式区分不同会话：
- 请求头 `X-Session-ID`
- 请求头 `X-User-ID`
- 请求体 `user` 字段
""",
    version="1.2.0",
    lifespan=lifespan,
    contact={
        "name": "ADK Middleware",
    },
    license_info={
        "name": "MIT",
    },
    openapi={
        "components": {
            "securitySchemes": security_scheme
        }
    },
)

# Add middlewares
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestTrackingMiddleware)


# Custom error response format
def error_response(message: str, error_type: str, code: int, details: dict = None) -> JSONResponse:
    """Create a standardized error response"""
    content = {
        "error": {
            "message": message,
            "type": error_type,
            "code": code
        }
    }
    if details:
        content["error"]["details"] = details

    request_id = get_request_id()
    if request_id:
        content["error"]["request_id"] = request_id

    return JSONResponse(status_code=code, content=content)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with detailed error messages"""
    error_types = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        500: "internal_error",
        502: "bad_gateway",
        503: "service_unavailable",
        504: "gateway_timeout",
    }
    error_type = error_types.get(exc.status_code, "unknown_error")

    logger.warning(f"HTTP {exc.status_code}: {exc.detail}")
    return error_response(str(exc.detail), error_type, exc.status_code)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle validation errors"""
    logger.warning(f"Validation error: {exc}")
    return error_response(str(exc), "validation_error", 400)


@app.exception_handler(httpx.TimeoutException)
async def timeout_error_handler(request: Request, exc: httpx.TimeoutException):
    """Handle timeout errors"""
    logger.error(f"Request timeout: {exc}")
    return error_response(
        "Request timed out while communicating with ADK backend",
        "timeout_error",
        504
    )


@app.exception_handler(httpx.HTTPStatusError)
async def http_status_error_handler(request: Request, exc: httpx.HTTPStatusError):
    """Handle ADK HTTP errors with detailed messages"""
    status = exc.response.status_code

    # Try to extract error details from ADK response
    try:
        error_body = exc.response.json()
        adk_message = error_body.get('error', {}).get('message', str(error_body))
    except:
        adk_message = exc.response.reason_phrase

    logger.error(f"ADK HTTP error {status}: {adk_message}")

    if status >= 500:
        return error_response(
            f"ADK backend error: {adk_message}",
            "adk_backend_error",
            502,
            {"adk_status": status}
        )
    elif status == 400:
        return error_response(
            f"Request rejected by ADK: {adk_message}",
            "adk_bad_request",
            400,
            {"adk_status": status}
        )
    elif status == 404:
        return error_response(
            "ADK resource not found. Session may have expired.",
            "adk_not_found",
            404,
            {"adk_status": status}
        )
    else:
        return error_response(
            f"ADK request failed: {adk_message}",
            "adk_error",
            status,
            {"adk_status": status}
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unexpected errors"""
    request_id = get_request_id()
    logger.error(f"Unhandled exception [request_id={request_id}]: {exc}", exc_info=True)
    return error_response(
        "An unexpected error occurred",
        "internal_error",
        500,
        {"request_id": request_id} if request_id else None
    )


@app.post(
    "/v1/chat/completions",
    openapi_extra={
        "security": [{"BearerAuth": []}],
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "simple": {
                            "summary": "简单文本对话",
                            "value": {
                                "model": "agent",
                                "messages": [{"role": "user", "content": "你好"}]
                            }
                        },
                        "streaming": {
                            "summary": "流式响应",
                            "value": {
                                "model": "agent",
                                "messages": [{"role": "user", "content": "请详细介绍一下"}],
                                "stream": True
                            }
                        },
                        "with_image": {
                            "summary": "带图片",
                            "value": {
                                "model": "agent",
                                "messages": [{
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": "描述这张图片"},
                                        {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                                    ]
                                }]
                            }
                        },
                        "with_audio": {
                            "summary": "带音频",
                            "value": {
                                "model": "agent",
                                "messages": [{
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": "这段音频说了什么"},
                                        {"type": "audio_url", "audio_url": {"url": "https://example.com/audio.mp3"}}
                                    ]
                                }]
                            }
                        },
                        "with_session": {
                            "summary": "指定会话",
                            "value": {
                                "model": "agent",
                                "user": "my_session_123",
                                "messages": [{"role": "user", "content": "继续之前的对话"}]
                            }
                        }
                    }
                }
            }
        }
    }
)
async def create_chat_completion(
    request: ChatCompletionRequest,
    http_request: Request,
    api_key_valid: bool = Depends(verify_api_key_dependency),
    x_session_id: Annotated[Optional[str], Header(description="会话 ID，用于区分不同会话")] = None,
    x_user_id: Annotated[Optional[str], Header(description="用户 ID，用于区分不同用户")] = None,
    x_request_id: Annotated[Optional[str], Header(description="请求 ID，用于追踪请求")] = None,
):
    """
    Create a chat completion.

    ## 请求头参数

    | Header | 说明 | 示例 |
    |--------|------|------|
    | `X-Session-ID` | 会话标识 | `conversation_123` |
    | `X-User-ID` | 用户标识 | `user_abc` |
    | `X-Request-ID` | 请求追踪 ID | `req_123` |
    | `Authorization` | API Key | `Bearer sk-xxx` |

    ## 支持的内容类型

    - `text`: 纯文本消息
    - `image_url`: 图片 URL 或 Base64
    - `audio_url`: 音频 URL
    - `video_url`: 视频 URL
    - `input_audio`: OpenAI 格式音频
    - `file`: 通用文件 URL

    ## 流式响应

    设置 `stream: true` 启用 SSE 流式输出。

    ## 会话管理

    通过以下方式区分会话：
    - 请求头 `X-Session-ID`
    - 请求头 `X-User-ID`
    - 请求体 `user` 字段
    """
    # 从请求头获取 Session ID 或 User ID（支持多会话）
    # 优先使用显式参数，否则从 http_request 获取
    session_id_override = x_session_id or http_request.headers.get("X-Session-ID")
    user_id_override = x_user_id or http_request.headers.get("X-User-ID")

    if session_id_override:
        request.user = session_id_override
        logger.info(f"Session from header: {session_id_override}")
    elif user_id_override:
        request.user = user_id_override
        logger.info(f"User from header: {user_id_override}")
    elif not request.user:
        request.user = f"temp_{uuid.uuid4().hex[:8]}"
        logger.info(f"Generated temp session: {request.user}")

    logger.info(f"Chat request: model={request.model}, user={request.user}, stream={request.stream}, messages={len(request.messages)}")

    # Validate request
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages cannot be empty")

    # Validate last message is from user
    if request.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="Last message must be from user")

    if request.stream:
        # For streaming, wrap the generator to save history after completion
        return StreamingResponse(
            _stream_with_history(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "X-Request-ID": get_request_id() or "",
            }
        )
    else:
        start_time = time.time()
        response = await adk_client.create_chat_completion(request)
        latency_ms = int((time.time() - start_time) * 1000)

        # Save to history
        if settings.session_history_enabled and db:
            _save_message_to_history(
                session_id=request.user,
                user_id=request.user,
                request=request,
                response=response,
                latency_ms=latency_ms
            )

        logger.info(f"Chat response generated for user: {request.user}")
        return response


async def _stream_with_history(request: ChatCompletionRequest):
    """Wrapper generator that saves history after streaming completes."""
    start_time = time.time()
    full_content = ""
    request_id = get_request_id()

    async for chunk in adk_client.create_chat_completion_stream(request):
        # Extract content from chunk for history
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                data = json.loads(chunk[6:])
                if data.get("choices"):
                    delta = data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    full_content += content
            except:
                pass

        yield chunk

    # Save to history after streaming completes
    latency_ms = int((time.time() - start_time) * 1000)

    if settings.session_history_enabled and db and full_content:
        try:
            # Save user message
            user_content = ""
            if request.messages and request.messages[-1]:
                last_msg = request.messages[-1]
                if isinstance(last_msg.content, str):
                    user_content = last_msg.content
                elif isinstance(last_msg.content, list):
                    for part in last_msg.content:
                        if hasattr(part, 'text') and part.text:
                            user_content += part.text

            db.save_message(
                session_id=request.user,
                user_id=request.user,
                app_name=settings.adk_app_name,
                role="user",
                content=user_content,
                request_id=request_id
            )

            # Save assistant message
            db.save_message(
                session_id=request.user,
                user_id=request.user,
                app_name=settings.adk_app_name,
                role="assistant",
                content=full_content,
                request_id=request_id,
                model=request.model,
                latency_ms=latency_ms
            )
        except Exception as e:
            logger.error(f"Failed to save streaming history: {e}")


def _save_message_to_history(
    session_id: str,
    user_id: str,
    request: ChatCompletionRequest,
    response: ChatCompletionResponse,
    latency_ms: int
):
    """Save request and response to history."""
    try:
        request_id = get_request_id()

        # Extract user message content
        user_content = ""
        if request.messages and request.messages[-1]:
            last_msg = request.messages[-1]
            if isinstance(last_msg.content, str):
                user_content = last_msg.content
            elif isinstance(last_msg.content, list):
                for part in last_msg.content:
                    if hasattr(part, 'text') and part.text:
                        user_content += part.text

        # Save user message
        db.save_message(
            session_id=session_id,
            user_id=user_id,
            app_name=settings.adk_app_name,
            role="user",
            content=user_content[:10000],  # Limit content length
            request_id=request_id
        )

        # Extract assistant response content
        assistant_content = ""
        if response.choices and response.choices[0].message:
            assistant_content = response.choices[0].message.content or ""

        # Save assistant message
        db.save_message(
            session_id=session_id,
            user_id=user_id,
            app_name=settings.adk_app_name,
            role="assistant",
            content=assistant_content[:10000],
            request_id=request_id,
            model=request.model,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"Failed to save message to history: {e}")


@app.get("/v1/models")
async def list_models(
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> ListModelsResponse:
    """List available models (ADK agents)."""
    logger.info("Models list request")
    return await adk_client.list_models()


@app.get("/v1/health")
async def health_check() -> dict:
    """Basic health check endpoint."""
    return {"status": "ok"}


@app.get("/v1/health/detailed")
async def health_check_detailed() -> dict:
    """
    Detailed health check including ADK backend status.
    No authentication required for monitoring systems.
    """
    logger.info("Detailed health check request")
    health_status = await adk_client.check_health()
    return health_status


@app.get("/v1/sessions")
async def list_sessions(
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """List all sessions (from database if history enabled, otherwise from ADK cache)."""
    if settings.session_history_enabled and db:
        # Return sessions from database
        sessions = db.list_sessions(limit=100)
        return {
            "source": "database",
            "count": len(sessions),
            "sessions": sessions
        }
    else:
        # Return sessions from ADK cache
        sessions = adk_client.list_cached_sessions()
        return {
            "source": "adk_cache",
            "count": len(sessions),
            "sessions": sessions
        }


@app.delete("/v1/sessions/{session_id}")
async def delete_session(
    session_id: str,
    app_name: str = None,
    user_id: str = None,
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """
    Delete a specific session.

    Parameters:
    - session_id: The session ID to delete
    - app_name: App name (defaults to configured ADK_APP_NAME)
    - user_id: User ID (defaults to 'anonymous')
    """
    app_name = app_name or settings.adk_app_name
    user_id = user_id or "anonymous"

    logger.info(f"Delete session request: {session_id} (app={app_name}, user={user_id})")
    result = await adk_client.delete_session(app_name, user_id, session_id)
    return result


@app.post("/v1/sessions/{session_id}/reset")
async def reset_session(
    session_id: str,
    app_name: str = None,
    user_id: str = None,
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """
    Reset a session by deleting and recreating it.
    Use this when a session becomes corrupted.

    Parameters:
    - session_id: The session ID to reset
    - app_name: App name (defaults to configured ADK_APP_NAME)
    - user_id: User ID (defaults to 'anonymous')
    """
    app_name = app_name or settings.adk_app_name
    user_id = user_id or "anonymous"

    logger.info(f"Reset session request: {session_id} (app={app_name}, user={user_id})")
    result = await adk_client.reset_session(app_name, user_id, session_id)
    return result


# ============ Session History API ============

@app.get("/v1/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    limit: int = 100,
    offset: int = 0,
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """
    Get message history for a session.

    Parameters:
    - session_id: The session ID
    - limit: Maximum number of messages to return (default 100)
    - offset: Number of messages to skip (default 0)
    """
    if not settings.session_history_enabled or not db:
        raise HTTPException(status_code=503, detail="Session history is not enabled")

    session_info = db.get_session_info(session_id)
    if not session_info:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.get_session_history(session_id, limit=limit, offset=offset)

    return {
        "session_id": session_id,
        "session_info": session_info,
        "messages": messages,
        "count": len(messages)
    }


@app.get("/v1/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = "json",
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """
    Export a session's complete history.

    Parameters:
    - session_id: The session ID
    - format: Export format (json or markdown)
    """
    if not settings.session_history_enabled or not db:
        raise HTTPException(status_code=503, detail="Session history is not enabled")

    session_info = db.get_session_info(session_id)
    if not session_info:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.get_session_history(session_id, limit=10000)

    if format == "markdown":
        # Generate markdown format
        lines = [f"# Session: {session_id}", ""]
        lines.append(f"- Created: {session_info['created_at']}")
        lines.append(f"- Messages: {session_info['message_count']}")
        lines.append("")
        lines.append("## Conversation")
        lines.append("")

        for msg in messages:
            role = msg['role'].upper()
            content = msg['content'] or ''
            lines.append(f"**{role}:** {content}")
            lines.append("")

        return {
            "format": "markdown",
            "content": "\n".join(lines)
        }
    else:
        return {
            "format": "json",
            "session_info": session_info,
            "messages": messages
        }


@app.delete("/v1/sessions/{session_id}/history")
async def delete_session_history(
    session_id: str,
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """
    Delete all history for a session.

    This will remove all message records but keep the session active in ADK.
    """
    if not settings.session_history_enabled or not db:
        raise HTTPException(status_code=503, detail="Session history is not enabled")

    deleted_count = db.delete_session_history(session_id)

    return {
        "success": True,
        "session_id": session_id,
        "deleted_messages": deleted_count
    }


@app.get("/v1/history/search")
async def search_history(
    q: str,
    session_id: str = None,
    limit: int = 50,
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """
    Search messages across all sessions.

    Parameters:
    - q: Search query
    - session_id: Limit to specific session (optional)
    - limit: Maximum results (default 50)
    """
    if not settings.session_history_enabled or not db:
        raise HTTPException(status_code=503, detail="Session history is not enabled")

    results = db.search_messages(q, session_id=session_id, limit=limit)

    return {
        "query": q,
        "count": len(results),
        "results": results
    }


@app.get("/v1/history/stats")
async def get_history_stats(
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """Get session history statistics."""
    if not settings.session_history_enabled or not db:
        return {"enabled": False}

    stats = db.get_stats()
    stats["enabled"] = True
    stats["database_path"] = settings.database_path
    return stats


@app.post("/v1/history/cleanup")
async def cleanup_history(
    days: int = 30,
    api_key_valid: bool = Depends(verify_api_key_dependency)
) -> dict:
    """
    Clean up old session history.

    Parameters:
    - days: Delete sessions older than this many days (default 30)
    """
    if not settings.session_history_enabled or not db:
        raise HTTPException(status_code=503, detail="Session history is not enabled")

    deleted_count = db.cleanup_old_sessions(days=days)

    return {
        "success": True,
        "deleted_sessions": deleted_count,
        "older_than_days": days
    }


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    api_key: HTTPAuthorizationCredentials = Depends(verify_api_key_dependency)
) -> Dict[str, Any]:
    """Upload file and convert to Base64 format."""
    from app.multimodal import MultimodalProcessor
    processor = MultimodalProcessor()

    logger.info(f"File upload: {file.filename}, content_type={file.content_type}")

    # Check file size first
    file_content = await file.read()
    file_size = len(file_content)
    max_size = settings.max_file_size_mb * 1024 * 1024

    if file_size > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File size ({file_size / 1024 / 1024:.1f}MB) exceeds limit ({settings.max_file_size_mb}MB)"
        )

    # Validate and process file
    is_valid, error_msg, detected_mime = processor.validate_file(
        file_content, file.filename, file.content_type
    )

    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # Convert to Base64
    inline_data, extracted_text = processor.process_base64_file(
        base64.b64encode(file_content).decode('utf-8'),
        file.filename,
        detected_mime
    )

    if inline_data:
        logger.info(f"File processed as binary: {detected_mime}")
        return {
            "success": True,
            "filename": file.filename,
            "mime_type": inline_data.mimeType,
            "base64_data": inline_data.data,
            "size": file_size,
            "type": "binary"
        }
    elif extracted_text:
        logger.info(f"File processed as text: {len(extracted_text)} chars extracted")
        return {
            "success": True,
            "filename": file.filename,
            "original_mime_type": detected_mime,
            "extracted_text": extracted_text,
            "text_length": len(extracted_text),
            "size": file_size,
            "type": "text"
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to process file")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "ADK Middleware API is running",
        "version": "1.1.0",
        "request_id": get_request_id()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower()
    )
