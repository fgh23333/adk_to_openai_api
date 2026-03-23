import json
import time
from typing import AsyncGenerator, Optional
import httpx
from contextlib import asynccontextmanager
from app.core.config import settings
from app.core.backend_manager import get_backend_manager
from app.schemas.models import (
    ChatCompletionRequest, ChatCompletionResponse, ChatCompletionResponseChoice,
    ChatMessage, ADKRunRequest, ADKMessage, ADKPart, ListModelsResponse, ModelInfo
)
from app.utils.multimodal import MultimodalProcessor
import logging

logger = logging.getLogger(__name__)

# 共享的连接池配置（用于所有客户端实例）
_HTTP_LIMITS = httpx.Limits(
    max_connections=200,      # 增加最大连接数
    max_keepalive_connections=50,
    keepalive_expiry=30.0
)


async def get_http_client() -> httpx.AsyncClient:
    """
    获取一个新的 HTTP 客户端实例（每个请求独立）。

    使用 context manager 自动管理连接生命周期。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(240.0, connect=10.0),
        limits=_HTTP_LIMITS,
        http2=True
    )


class ADKClient:
    """
    ADK 客户端 - 无状态设计

    注意：此类不再维护内部的 HTTP 客户端实例。
    每个请求应该使用 get_http_client() 获取独立的客户端。
    """

    def __init__(self):
        self.multimodal_processor = MultimodalProcessor()
        # 会话缓存保留（线程安全）
        self._session_cache = set()

    def get_backend_url(self, mapping_key: str) -> str:
        """
        根据映射 key 获取对应的后端地址

        Args:
            mapping_key: 映射 key（如 data-analysis）

        Returns:
            backend_url

        Raises:
            ValueError: 如果后端未配置或已禁用
        """
        backend_manager = get_backend_manager()
        url = backend_manager.get_backend_url(mapping_key)
        if url is None:
            available = backend_manager.get_all_enabled_keys()
            raise ValueError(
                f"Backend '{mapping_key}' not configured or disabled. "
                f"Available backends: {available}"
            )
        return url

    async def close(self):
        """不再需要关闭客户端（每个请求独立管理）"""
        logger.info("ADKClient close() called (no-op, clients managed per request)")

    @asynccontextmanager
    async def _get_client_context(self):
        """Context manager for HTTP client - 每次创建新实例"""
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(240.0, connect=10.0),
            limits=_HTTP_LIMITS,
            http2=True
        )
        try:
            yield client
        finally:
            await client.aclose()

    async def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Create a non-streaming chat completion."""
        adk_request = await self._convert_to_adk_request(request)

        # 获取对应的后端地址
        backend_url = self.get_backend_url(adk_request._mapping_key)

        # Ensure session exists before running
        await self._ensure_session(adk_request._agent_name, adk_request.userId, adk_request.sessionId, backend_url)

        # Log the request for debugging
        request_data = adk_request.to_adk_format()
        logger.info(f"Sending ADK request to {backend_url}/run (app={adk_request.appName})")
        logger.debug(f"Request data: {request_data}")

        # 使用独立的客户端
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(240.0, connect=10.0),
            limits=_HTTP_LIMITS,
            http2=True
        ) as client:
            try:
                response = await client.post(
                    f"{backend_url}/run",
                    json=request_data
                )
                logger.info(f"ADK response status: {response.status_code}")

                if response.status_code == 200:
                    adk_response = response.json()
                    return self._convert_from_adk_response(adk_response, request.model)

                response.raise_for_status()

            except httpx.HTTPStatusError as e:
                logger.error(f"ADK HTTP error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Error calling ADK: {e}")
                raise

    async def create_chat_completion_stream(self, request: ChatCompletionRequest) -> AsyncGenerator[str, None]:
        """Create a streaming chat completion using ADK SSE endpoint."""
        adk_request = await self._convert_to_adk_request(request)
        adk_request.streaming = True

        # 获取对应的后端地址
        backend_url = self.get_backend_url(adk_request._mapping_key)

        # Ensure session exists before running
        await self._ensure_session(adk_request._agent_name, adk_request.userId, adk_request.sessionId, backend_url)

        # Log the request for debugging
        request_data = adk_request.to_adk_format()
        logger.info(f"Sending ADK request to {backend_url}/run_sse (real streaming, app={adk_request.appName})")

        # Track sent content for deduplication
        sent_content_tracker = {}
        tracker_key = f"{request.user or 'default'}:{int(time.time())}"
        sent_content_tracker[tracker_key] = ""

        # 使用独立的客户端
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(240.0, connect=10.0),
            limits=_HTTP_LIMITS,
            http2=True
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{backend_url}/run_sse",
                    json=request_data,
                    headers={"Accept": "text/event-stream"}
                ) as response:
                    logger.info(f"ADK SSE response status: {response.status_code}")

                    if response.status_code != 200:
                        error_body = await response.aread()
                        logger.error(f"ADK SSE error: {response.status_code} - {error_body}")
                        yield self._create_error_chunk(request.model, f"ADK error: {response.status_code}")
                        yield "data: [DONE]\n\n"
                        return

                    chat_id = f"chatcmpl-{int(time.time())}"

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue

                        # Parse SSE line
                        if line.startswith("data:"):
                            data_str = line[5:].strip()

                            if data_str == "[DONE]":
                                yield self._create_final_chunk(chat_id, request.model)
                                yield "data: [DONE]\n\n"
                                return

                            try:
                                event_data = json.loads(data_str)
                                chunk = self._convert_adk_sse_to_openai(
                                    event_data,
                                    request.model,
                                    chat_id,
                                    sent_content_tracker[tracker_key]
                                )

                                if chunk:
                                    if "choices" in chunk and chunk["choices"]:
                                        delta = chunk["choices"][0].get("delta", {})
                                        new_content = delta.get("content", "")
                                        if new_content:
                                            sent_content_tracker[tracker_key] += new_content

                                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse SSE data: {data_str[:100]}")
                                continue

                    yield self._create_final_chunk(chat_id, request.model)
                    yield "data: [DONE]\n\n"

            except httpx.TimeoutException:
                logger.error("ADK SSE timeout")
                yield self._create_error_chunk(request.model, "Request timeout")
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"Error in ADK SSE stream: {e}")
                yield self._create_error_chunk(request.model, str(e))
                yield "data: [DONE]\n\n"

    def _convert_adk_sse_to_openai(self, adk_event: dict, model: str, chat_id: str, previously_sent: str) -> Optional[dict]:
        """
        Convert ADK SSE event to OpenAI streaming chunk format.

        处理各种 ADK 事件类型：
        - content.parts[].text: 文本内容
        - functionCall: 工具调用（需要记录但不发送内容）
        - functionResponse: 工具响应（需要记录但不发送内容）
        - agent: 代理路由（需要记录但不发送内容）
        """
        try:
            content = ""
            event_type = adk_event.get("event", "unknown")

            # 记录事件类型用于调试
            if event_type != "content":
                logger.debug(f"ADK event type: {event_type}, keys: {list(adk_event.keys())}")

            # 处理文本内容
            if "content" in adk_event:
                content_part = adk_event["content"]
                if isinstance(content_part, dict) and "parts" in content_part:
                    for part in content_part["parts"]:
                        if "text" in part:
                            content += part["text"]
                elif isinstance(content_part, str):
                    content = content_part
            elif "text" in adk_event:
                content = adk_event["text"]
            elif "data" in adk_event and isinstance(adk_event["data"], str):
                content = adk_event["data"]

            # 处理工具调用事件（记录但不发送内容）
            if "functionCall" in adk_event:
                func_call = adk_event["functionCall"]
                logger.info(f"ADK function call: {func_call.get('name', 'unknown')}")
                # 工具调用不产生文本内容，返回空但不中断流
                return {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": None
                    }]
                }

            # 处理工具响应事件（记录但不发送内容）
            if "functionResponse" in adk_event:
                func_response = adk_event["functionResponse"]
                logger.info(f"ADK function response from: {func_response.get('name', 'unknown')}")
                # 工具响应不产生文本内容，返回空但不中断流
                return {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": None
                    }]
                }

            # 处理代理路由事件
            if "agent" in adk_event:
                agent_info = adk_event["agent"]
                logger.info(f"ADK agent routing: {agent_info.get('name', 'unknown')}")
                # 代理路由不产生文本内容，返回空但不中断流
                return {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": None
                    }]
                }

            # 如果没有文本内容，返回空 chunk 但不中断流
            if not content:
                return {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": None
                    }]
                }

            new_content = self._extract_new_content(content, previously_sent)

            if not new_content:
                return {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": None
                    }]
                }

            return {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": new_content},
                    "finish_reason": None
                }]
            }

        except Exception as e:
            logger.error(f"Error converting ADK SSE event: {e}")
            # 出错时返回空 chunk 而不是 None，避免中断流
            return {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": None
                }]
            }

    def _extract_new_content(self, current_content: str, previously_sent: str) -> str:
        """Extract only new content that hasn't been sent yet."""
        if not previously_sent:
            return current_content

        if current_content == previously_sent:
            return ""

        if current_content.startswith(previously_sent):
            return current_content[len(previously_sent):]

        logger.warning(f"Content reset detected, sending full content")
        return current_content

    def _create_final_chunk(self, chat_id: str, model: str) -> str:
        """Create the final streaming chunk with finish_reason."""
        chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    def _create_error_chunk(self, model: str, error_message: str) -> str:
        """Create an error chunk."""
        chunk = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": f"[Error: {error_message}]"},
                "finish_reason": "error"
            }]
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    async def list_apps(self, backend_url: str) -> list:
        """从 ADK 后端获取应用列表"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{backend_url}/list-apps")
                response.raise_for_status()
                data = response.json()
                logger.info(f"Got apps list from {backend_url}/list-apps: {data}")
                return data
        except Exception as e:
            logger.warning(f"Failed to get apps list from {backend_url}: {e}")
            return []

    async def list_models(self, request_model: str = None) -> ListModelsResponse:
        """List available models (ADK agents)."""
        models = []
        seen_models = set()

        backends_to_query = {}

        if request_model and "/" in request_model:
            mapping_key, _ = settings.parse_model(request_model)
            backend_url = self.get_backend_url(mapping_key)
            backends_to_query[mapping_key] = backend_url
        else:
            backends_to_query = get_backend_manager().get_all_enabled_backends().copy()

        for mapping_key, backend_url in backends_to_query.items():
            try:
                apps_data = await self.list_apps(backend_url)

                if isinstance(apps_data, list):
                    for item in apps_data:
                        if isinstance(item, dict):
                            agent_name = item.get("name", "")
                            if agent_name:
                                model_id = settings.format_model(mapping_key, agent_name)
                                if model_id not in seen_models:
                                    seen_models.add(model_id)
                                    models.append(ModelInfo(
                                        id=model_id,
                                        created=int(time.time()),
                                        owned_by=mapping_key
                                    ))
                        elif isinstance(item, str):
                            model_id = settings.format_model(mapping_key, item)
                            if model_id not in seen_models:
                                seen_models.add(model_id)
                                models.append(ModelInfo(
                                    id=model_id,
                                    created=int(time.time()),
                                    owned_by=mapping_key
                                ))
            except Exception as e:
                logger.warning(f"Failed to get models from {backend_url}: {e}")

        return ListModelsResponse(data=models)

    async def _convert_to_adk_request(self, request: ChatCompletionRequest) -> ADKRunRequest:
        """Convert OpenAI request to ADK request format."""
        if not request.model:
            raise ValueError(
                "Model is required. Must use 'app_name/agent_name' format. "
                f"Available apps: {get_backend_manager().get_all_enabled_keys()}"
            )

        app_name, agent_name = settings.parse_model(request.model)
        logger.info(f"Parsed model: app={app_name}, agent={agent_name}")

        last_message = request.messages[-1] if request.messages else None

        if not last_message or last_message.role != "user":
            raise ValueError("Last message must be from user")

        user_id = request.user or "anonymous"
        session_id = f"session_{user_id}"

        if isinstance(last_message.content, str):
            logger.info(f"Processing simple text content: {last_message.content[:100]}...")
            adk_parts = [ADKPart(text=last_message.content)]
        else:
            logger.info(f"Processing multimodal content with {len(last_message.content)} parts")
            _, adk_parts = await self.multimodal_processor.process_content(last_message.content)
            logger.info(f"Processed into {len(adk_parts)} ADK parts")

        adk_message = ADKMessage(
            role="user",
            parts=adk_parts
        )

        adk_request = ADKRunRequest(
            appName=agent_name,
            userId=user_id,
            sessionId=session_id,
            streaming=request.stream,
            newMessage=adk_message
        )

        adk_request._original_model = request.model
        adk_request._mapping_key = app_name
        adk_request._agent_name = agent_name

        return adk_request

    def _convert_from_adk_response(self, adk_response, model: str) -> ChatCompletionResponse:
        """
        Convert ADK response to OpenAI response format.

        处理各种 ADK 响应类型：
        - 列表：取最后一个响应
        - 字典：提取 content.parts[].text
        - 工具调用响应：处理 functionCall/functionResponse
        """
        logger.info(f"Converting ADK response of type: {type(adk_response)}")

        if isinstance(adk_response, list):
            logger.info(f"ADK returned list with {len(adk_response)} items")
            if not adk_response:
                content = ""
            else:
                last_response = adk_response[-1]
                logger.info(f"Using last response: {type(last_response)}")
                return self._convert_from_adk_response(last_response, model)
        elif not isinstance(adk_response, dict):
            logger.error(f"Unexpected ADK response type: {type(adk_response)}")
            content = str(adk_response)
        else:
            content = ""

            # 记录响应中的所有字段（用于调试）
            response_keys = list(adk_response.keys())
            logger.debug(f"ADK response keys: {response_keys}")

            # 检查是否有工具调用
            if "functionCall" in adk_response:
                func_call = adk_response["functionCall"]
                logger.info(f"Response contains function call: {func_call.get('name', 'unknown')}")

            # 检查是否有工具响应
            if "functionResponse" in adk_response:
                func_response = adk_response["functionResponse"]
                logger.info(f"Response contains function response from: {func_response.get('name', 'unknown')}")

            # 检查是否有代理路由
            if "agent" in adk_response:
                agent_info = adk_response["agent"]
                logger.info(f"Response contains agent routing: {agent_info.get('name', 'unknown')}")

            # 提取文本内容
            if "content" in adk_response:
                content_part = adk_response["content"]
                if isinstance(content_part, dict) and "parts" in content_part:
                    for part in content_part["parts"]:
                        if "text" in part:
                            content += part["text"]
                elif isinstance(content_part, str):
                    content = content_part
                elif isinstance(content_part, list):
                    for part in content_part:
                        if isinstance(part, dict) and "text" in part:
                            content += part["text"]
                        elif isinstance(part, str):
                            content += part
            elif "text" in adk_response:
                content = adk_response["text"]
            else:
                # 尝试从其他字段提取内容
                logger.warning(f"ADK response structure: {response_keys}")
                if isinstance(adk_response, dict):
                    for key, value in adk_response.items():
                        if isinstance(value, str) and len(value) > 10:
                            content = value
                            logger.info(f"Using content from key '{key}': {content[:100]}...")
                            break

        logger.info(f"Final extracted content: {content[:100] if content else '(empty)'}... (length: {len(content)})")

        response = ChatCompletionResponse(
            id=f"chatcmpl-{int(time.time())}",
            created=int(time.time()),
            model=model,
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=content or ""),
                    finish_reason="stop"
                )
            ]
        )

        return response

    async def _ensure_session(self, agent_name: str, user_id: str, session_id: str, backend_url: str = None):
        """Ensure session exists before running agent."""
        session_key = f"{agent_name}:{user_id}:{session_id}"

        if session_key in self._session_cache:
            logger.debug(f"Session already in cache: {session_key}")
            return

        if backend_url is None:
            raise ValueError("backend_url must be provided when calling _ensure_session")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"Creating ADK session: {session_id} for agent={agent_name}, user={user_id} at {backend_url}")
                response = await client.post(
                    f"{backend_url}/apps/{agent_name}/users/{user_id}/sessions",
                    json={"sessionId": session_id}
                )

                logger.info(f"Session creation response: {response.status_code}")

                if response.status_code in [200, 201]:
                    logger.info(f"Created ADK session: {session_id}")
                    self._session_cache.add(session_key)
                elif response.status_code == 409:
                    logger.info(f"ADK session already exists: {session_id}")
                    self._session_cache.add(session_key)
                else:
                    logger.warning(f"Failed to create ADK session: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"Error ensuring ADK session: {e}")

    async def _delete_session(self, agent_name: str, user_id: str, session_id: str, backend_url: str = None) -> bool:
        """Delete an ADK session."""
        session_key = f"{agent_name}:{user_id}:{session_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"Deleting ADK session: {session_id} at {backend_url}")
                response = await client.delete(
                    f"{backend_url}/apps/{agent_name}/users/{user_id}/sessions/{session_id}"
                )

                if response.status_code in [200, 204, 404]:
                    logger.info(f"Deleted ADK session: {session_id}")
                    self._session_cache.discard(session_key)
                    return True
                else:
                    logger.warning(f"Failed to delete session: {response.status_code}")
                    return False

        except Exception as e:
            logger.error(f"Error deleting ADK session: {e}")
            return False

    async def _reset_session(self, agent_name: str, user_id: str, session_id: str, backend_url: str):
        """Reset a corrupted session by deleting and recreating it."""
        session_key = f"{agent_name}:{user_id}:{session_id}"

        logger.warning(f"Resetting corrupted session: {session_id}")

        await self._delete_session(agent_name, user_id, session_id, backend_url)

        self._session_cache.discard(session_key)

        await self._ensure_session(agent_name, user_id, session_id, backend_url)

    # ============ Public API Methods ============

    async def check_health(self) -> dict:
        """Check health status of all configured ADK backends."""
        backends = get_backend_manager().get_all_enabled_backends()

        if not backends:
            return {
                "middleware": "healthy",
                "status": "error",
                "error": "No backends configured in ADK_BACKEND_MAPPING",
                "backends": {}
            }

        result = {
            "middleware": "healthy",
            "status": "healthy",
            "backends": {}
        }

        all_healthy = True

        for app_name, backend_url in backends.items():
            backend_result = {
                "url": backend_url,
                "status": "unknown"
            }

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    start_time = time.time()
                    response = await client.get(f"{backend_url}/")
                    latency = (time.time() - start_time) * 1000

                    if response.status_code < 500:
                        backend_result["status"] = "healthy"
                        backend_result["latency_ms"] = round(latency, 2)
                    else:
                        backend_result["status"] = "unhealthy"
                        backend_result["error"] = f"HTTP {response.status_code}"
                        all_healthy = False

            except httpx.TimeoutException:
                backend_result["status"] = "timeout"
                backend_result["error"] = "Connection timeout"
                all_healthy = False
            except httpx.ConnectError as e:
                backend_result["status"] = "unreachable"
                backend_result["error"] = str(e)
                all_healthy = False
            except Exception as e:
                backend_result["status"] = "error"
                backend_result["error"] = str(e)
                all_healthy = False

            result["backends"][app_name] = backend_result

        if not all_healthy:
            result["status"] = "degraded"

        return result

    async def delete_session(self, agent_name: str, user_id: str, session_id: str, mapping_key: str = None) -> dict:
        """Delete a specific session."""
        if mapping_key is None:
            mapping_key = agent_name
        backend_url = self.get_backend_url(mapping_key)
        success = await self._delete_session(agent_name, user_id, session_id, backend_url)
        return {
            "success": success,
            "session_id": session_id,
            "agent_name": agent_name,
            "user_id": user_id
        }

    async def reset_session(self, agent_name: str, user_id: str, session_id: str, mapping_key: str = None) -> dict:
        """Reset a session by deleting and recreating it."""
        if mapping_key is None:
            mapping_key = agent_name
        backend_url = self.get_backend_url(mapping_key)
        await self._reset_session(agent_name, user_id, session_id, backend_url)
        return {
            "success": True,
            "session_id": session_id,
            "agent_name": agent_name,
            "user_id": user_id,
            "action": "reset"
        }

    def list_cached_sessions(self) -> list:
        """List all sessions in local cache."""
        sessions = []
        for session_key in self._session_cache:
            parts = session_key.split(":")
            if len(parts) == 3:
                sessions.append({
                    "agent_name": parts[0],
                    "user_id": parts[1],
                    "session_id": parts[2]
                })
        return sessions
