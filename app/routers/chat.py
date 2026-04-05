"""
聊天完成接口路由
"""
from fastapi import APIRouter
from typing import Annotated, List, Optional, Union

from fastapi import HTTPException, Request, UploadFile, File, Header
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.config import settings
from app.core.adk_client import ADKClient
from app.core.metrics import get_metrics_collector, RequestMetrics
from app.core.auth import verify_api_key_dependency, auth
from app.schemas.models import (
    ChatCompletionRequest, ChatCompletionResponse,
    ListModelsResponse, ModelInfo,
    UploadBinaryResponse, UploadTextResponse, RootResponse
)
from app.database.database import get_database
import logging
import time
import uuid

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize ADK client
adk_client = ADKClient()

# Initialize database
db = None

# Initialize metrics collector
metrics_collector = get_metrics_collector()


def _generate_session_id_from_messages(messages: List, tenant_id: str) -> str:
    """
    Generate session ID based on conversation history hash.

    会话策略：
    1. 如果 messages 只有 1 条 → 自动创建新会话
    2. 如果 user 字段包含 "new:" 前缀 → 强制创建新会话
    3. 如果 user 字段包含 "reset:" 前缀 → 重置指定会话
    4. 否则 → 基于历史内容哈希生成会话 ID
    """
    # 检查是否为空或单条消息 → 自动新会话
    if not messages or len(messages) <= 1:
        return f"{tenant_id}_new_{uuid.uuid4().hex[:12]}"

    history_parts = []
    for msg in messages[:-1]:
        role = getattr(msg, 'role', '')
        content = getattr(msg, 'content', '')
        if isinstance(content, str):
            history_parts.append(f"{role}:{content[:100]}")  # 只取前100字符
        elif isinstance(content, list):
            for part in content:
                if hasattr(part, 'text') and part.text:
                    history_parts.append(f"{role}:{part.text[:100]}")

    if not history_parts:
        return f"{tenant_id}_new_{uuid.uuid4().hex[:12]}"

    import hashlib
    history_str = "|".join(history_parts)
    history_hash = hashlib.md5(history_str.encode()).hexdigest()[:12]

    return f"{tenant_id}_{history_hash}"


def _extract_app_name(model: str) -> str:
    """从 model 字符串中提取 app_name"""
    if "/" in model:
        return model.split("/", 1)[0]
    # 如果没有斜杠，返回整个 model（作为兼容）
    return model


@router.post("/v1/chat/completions")
async def create_chat_completion(
    request: ChatCompletionRequest,
    http_request: Request,
    api_key_valid: str = None,
    x_session_id: Annotated[Optional[str], Header()] = None,
    x_user_id: Annotated[Optional[str], Header()] = None,
    x_reset_session: Annotated[Optional[str], Header()] = None,
) -> ChatCompletionResponse:
    """
    Create a chat completion.

    Headers:
    - X-Session-ID: 指定会话 ID
    - X-User-ID: 指定用户 ID
    - X-Reset-Session: "true" 时重置会话上下文（开始新对话）
    """
    from app.core.auth import auth

    # Get tenant ID from API Key
    tenant_id = auth.get_session_id_from_api_key(api_key_valid)

    # Generate Session ID
    # 检查是否需要重置会话（多种方式）
    reset_session = False

    # 方式1: 通过 Header
    if x_reset_session == "true" or http_request.headers.get("X-Reset-Session") == "true":
        reset_session = True

    # 方式2: 通过 user 字段（Dify 可用）
    # user="new" → 强制新会话
    # user="reset:xxx" → 重置会话 xxx
    # user="session:xxx" → 使用指定会话
    if request.user:
        if request.user == "new" or request.user.startswith("new:"):
            reset_session = True
        elif request.user.startswith("session:"):
            # 使用指定会话 ID
            session_id = request.user[8:]  # 去掉 "session:" 前缀
            request.user = session_id
            logger.info(f"Using specified session: {session_id}")
            # 不需要继续生成，跳过后续逻辑
            reset_session = False
        elif request.user.startswith("reset:"):
            # 重置指定会话
            target_session = request.user[6:]  # 去掉 "reset:" 前缀
            session_id = f"{tenant_id}_new_{uuid.uuid4().hex[:12]}"
            logger.info(f"Resetting session {target_session}, new session: {session_id}")
            request.user = session_id
            reset_session = False  # 已处理，不需要再次重置

    if reset_session:
        # 强制创建新会话
        session_id = f"{tenant_id}_new_{uuid.uuid4().hex[:12]}"
        logger.info(f"Session reset requested, new session: {session_id}")
    else:
        session_id = _generate_session_id_from_messages(request.messages, tenant_id)

    # Allow override via headers
    session_id_override = x_session_id or http_request.headers.get("X-Session-ID")
    user_id_override = x_user_id or http_request.headers.get("X-User-ID")

    if session_id_override:
        session_id = session_id_override
    elif user_id_override:
        session_id = user_id_override
    elif request.user:
        session_id = request.user

    request.user = session_id
    logger.info(f"Chat request: tenant={tenant_id}, session={session_id}, model={request.model}, reset={reset_session}")

    # Validate request
    if not request.messages:
        raise HTTPException(status_code=400, detail="Messages cannot be empty")
    if request.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="Last message must be from user")

    # Start metrics collection
    request_id = f"req_{uuid.uuid4().hex[:8]}"
    req_metrics = await metrics_collector.start_request(
        request_id=request_id,
        tenant_id=tenant_id,
        session_id=session_id,
        model=request.model,
        is_streaming=request.stream
    )

    if request.stream:
        return StreamingResponse(
            _stream_with_history(request, req_metrics),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "X-Request-ID": request_id,
            }
        )
    else:
        start_time = time.time()
        try:
            response = await adk_client.create_chat_completion(request)
            latency_ms = int((time.time() - start_time) * 1000)

            # Estimate tokens
            input_text = ""
            for msg in request.messages:
                if isinstance(msg.content, str):
                    input_text += msg.content
                elif isinstance(msg.content, list):
                    for part in msg.content:
                        if hasattr(part, 'text') and part.text:
                            input_text += part.text

            output_text = response.choices[0].message.content if response.choices else ""
            input_tokens = len(input_text) // 4
            output_tokens = len(output_text) // 4

            await metrics_collector.end_request(
                metrics=req_metrics,
                success=True,
                input_tokens=input_tokens,
                output_tokens=output_tokens
            )

            # Save to history
            if settings.session_history_enabled and db:
                _save_message_to_history(
                    session_id=request.user,
                    user_id=request.user,
                    request=request,
                    response=response,
                    latency_ms=latency_ms
                )

            return response

        except Exception as e:
            await metrics_collector.end_request(
                metrics=req_metrics,
                success=False,
                error_type=type(e).__name__
            )
            raise


async def _stream_with_history(request: ChatCompletionRequest, req_metrics):
    """Wrapper generator for streaming responses."""
    from app.core.auth import auth

    start_time = time.time()
    full_content = ""
    request_id = f"req_{uuid.uuid4().hex[:8]}"

    async for chunk in adk_client.create_chat_completion_stream(request):
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                import json
                data = json.loads(chunk[6:])
                if data.get("choices"):
                    delta = data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    full_content += content
            except:
                pass
        yield chunk

    # Save to history after streaming
    latency_ms = int((time.time() - start_time) * 1000)

    if settings.session_history_enabled and db and full_content:
        try:
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
                app_name=_extract_app_name(request.model),
                role="user",
                content=user_content,
                request_id=request_id
            )

            db.save_message(
                session_id=request.user,
                user_id=request.user,
                app_name=_extract_app_name(request.model),
                role="assistant",
                content=full_content,
                request_id=request_id,
                model=request.model,
                latency_ms=latency_ms
            )
        except Exception as e:
            logger.error(f"Failed to save streaming history: {e}")

    # End metrics
    user_content = ""
    if request.messages and request.messages[-1]:
        last_msg = request.messages[-1]
        if isinstance(last_msg.content, str):
            user_content = last_msg.content
        elif isinstance(last_msg.content, list):
            for part in last_msg.content:
                if hasattr(part, 'text') and part.text:
                    user_content += part.text

    input_tokens = len(user_content) // 4
    output_tokens = len(full_content) // 4

    await metrics_collector.end_request(
        metrics=req_metrics,
        success=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens
    )


def _save_message_to_history(session_id, user_id, request, response, latency_ms):
    """Save request and response to history."""
    try:
        request_id = f"req_{uuid.uuid4().hex[:8]}"

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

        db.save_message(
            session_id=session_id,
            user_id=user_id,
            app_name=_extract_app_name(request.model),
            role="user",
            content=user_content[:10000],
            request_id=request_id
        )

        # Extract assistant response content
        assistant_content = ""
        if response.choices and response.choices[0].message:
            assistant_content = response.choices[0].message.content or ""

        db.save_message(
            session_id=session_id,
            user_id=user_id,
            app_name=_extract_app_name(request.model),
            role="assistant",
            content=assistant_content[:10000],
            request_id=request_id,
            model=request.model,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"Failed to save message to history: {e}")


@router.get("/v1/models")
async def list_models(
    api_key_valid: str = None,
    http_request: Request = None
) -> ListModelsResponse:
    """List available models (ADK agents)."""
    logger.info("Models list request")

    # Try to get model from query param
    query_model = None
    if http_request:
        query_model = http_request.query_params.get("model")
    logger.info(f"Query model: {query_model}")

    return await adk_client.list_models(request_model=query_model)


@router.post("/v1/upload", response_model=Union[UploadBinaryResponse, UploadTextResponse], summary="上传文件")
async def upload_file(
    file: UploadFile = File(...),
    api_key: str = None
):
    """Upload file and convert to Base64 format."""
    from app.utils.multimodal import MultimodalProcessor

    processor = MultimodalProcessor()

    logger.info(f"File upload: {file.filename}, content_type={file.content_type}")

    file_content = await file.read()
    file_size = len(file_content)
    max_size = settings.max_file_size_mb * 1024 * 1024

    if file_size > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File size ({file_size / 1024 / 1024:.1f}MB) exceeds limit ({settings.max_file_size_mb}MB)"
        )

    is_valid, error_msg, detected_mime = processor.validate_file(
        file_content, file.filename, file.content_type
    )

    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    import base64
    inline_data, extracted_text = processor.process_base64_file(
        base64.b64encode(file_content).decode('utf-8'),
        file.filename,
        detected_mime
    )

    if inline_data:
        return UploadBinaryResponse(
            filename=file.filename,
            mime_type=inline_data.mimeType,
            base64_data=inline_data.data,
            size=file_size
        )
    elif extracted_text:
        return UploadTextResponse(
            filename=file.filename,
            original_mime_type=detected_mime,
            extracted_text=extracted_text,
            text_length=len(extracted_text),
            size=file_size
        )
    else:
        raise HTTPException(status_code=500, detail="Failed to process file")


@router.get("/", response_model=RootResponse, summary="服务信息")
async def root():
    """Root endpoint - 服务基本信息。"""
    from app.core.config import get_request_id
    return RootResponse(
        message="ADK Middleware API is running",
        version="1.3.0",
        request_id=get_request_id()
    )


# ==================== 会话管理端点 ====================

@router.delete("/v1/sessions/{session_id}", summary="删除会话")
async def delete_session(
    session_id: str,
    agent_name: str = None,
    mapping_key: str = None,
    api_key_valid: str = None
):
    """
    删除指定会话，重置上下文。

    Args:
        session_id: 会话 ID
        agent_name: ADK agent 名称（可选，默认从 model 推断）
        mapping_key: 后端映射 key（可选）

    Headers:
        X-Agent-Name: 指定 agent 名称
        X-Mapping-Key: 指定后端映射 key
    """
    from app.core.auth import auth

    # 从 header 获取参数
    if not agent_name:
        agent_name = None  # ADKClient 会使用默认值

    if not mapping_key:
        mapping_key = None

    try:
        result = await adk_client.delete_session(
            agent_name=agent_name or "default",
            user_id=session_id.split("_")[0] if "_" in session_id else session_id,
            session_id=session_id,
            mapping_key=mapping_key
        )
        return result
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/sessions/reset", summary="重置会话")
async def reset_session_endpoint(
    request: Request,
    agent_name: str = None,
    mapping_key: str = None
):
    """
    重置会话上下文（删除并重新创建）。

    Body:
    {
        "session_id": "session_xxx",
        "agent_name": "my_agent",  // 可选
        "mapping_key": "app-name"   // 可选
    }

    或者通过 Header 指定：
    - X-Session-ID: 会话 ID
    - X-Agent-Name: agent 名称
    - X-Mapping-Key: 后端映射 key
    """
    from app.core.auth import auth
    from pydantic import BaseModel

    class ResetRequest(BaseModel):
        session_id: str
        agent_name: Optional[str] = None
        mapping_key: Optional[str] = None

    try:
        body = await request.json()
        reset_req = ResetRequest(**body)
    except:
        # 从 header 获取
        reset_req = ResetRequest(
            session_id=request.headers.get("X-Session-ID", ""),
            agent_name=request.headers.get("X-Agent-Name"),
            mapping_key=request.headers.get("X-Mapping-Key")
        )

    if not reset_req.session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    try:
        result = await adk_client.reset_session(
            agent_name=reset_req.agent_name or "default",
            user_id=reset_req.session_id.split("_")[0] if "_" in reset_req.session_id else reset_req.session_id,
            session_id=reset_req.session_id,
            mapping_key=reset_req.mapping_key
        )
        return result
    except Exception as e:
        logger.error(f"Error resetting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v1/sessions", summary="列本地缓存的会话")
async def list_sessions():
    """列出本地缓存的会话列表（仅供参考）。"""
    sessions = adk_client.list_cached_sessions()
    return {
        "count": len(sessions),
        "sessions": sessions
    }


@router.post("/v1/sessions/clear-cache", summary="清空本地会话缓存")
async def clear_session_cache():
    """
    清空本地会话缓存。

    注意：这只清空中间件的本地缓存，ADK 后端的会话仍然存在。
    要彻底删除会话，请使用 DELETE /v1/sessions/{session_id}
    """
    from app.core.adk_client import ADKClient
    # 创建新实例会清空缓存
    global adk_client
    adk_client = ADKClient()
    return {
        "success": True,
        "message": "Local session cache cleared"
    }
