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
    """Generate session ID based on conversation history hash."""
    if not messages or len(messages) <= 1:
        return f"{tenant_id}_new_{uuid.uuid4().hex[:8]}"

    history_parts = []
    for msg in messages[:-1]:
        role = getattr(msg, 'role', '')
        content = getattr(msg, 'content', '')
        if isinstance(content, str):
            history_parts.append(f"{role}:{content}")
        elif isinstance(content, list):
            for part in content:
                if hasattr(part, 'text') and part.text:
                    history_parts.append(f"{role}:{part.text}")

    if not history_parts:
        return f"{tenant_id}_new_{uuid.uuid4().hex[:8]}"

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
) -> ChatCompletionResponse:
    """Create a chat completion."""
    from app.core.auth import auth

    # Get tenant ID from API Key
    tenant_id = auth.get_session_id_from_api_key(api_key_valid)

    # Generate Session ID
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
    logger.info(f"Chat request: tenant={tenant_id}, session={session_id}, model={request.model}")

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
