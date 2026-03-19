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


class ADKClient:
    def __init__(self):
        self.multimodal_processor = MultimodalProcessor()
        self._session_cache = set()  # Simple cache for created sessions
        self._content_cache = {}  # Cache to track sent content for deduplication
        self._event_cache = set()  # Cache to track processed events for deduplication

        # Connection pool for better performance
        self._http_client: Optional[httpx.AsyncClient] = None
        self._http_limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30.0
        )

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

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with connection pooling."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(240.0, connect=10.0),
                limits=self._http_limits,
                http2=True  # Enable HTTP/2 for better performance
            )
        return self._http_client

    async def close(self):
        """Close the HTTP client and release resources."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            logger.info("HTTP client closed")

    @asynccontextmanager
    async def _get_client_context(self):
        """Context manager for HTTP client (for backward compatibility)."""
        client = await self._get_client()
        try:
            yield client
        except Exception:
            # Don't close on error, let the pool handle it
            raise
        
    async def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Create a non-streaming chat completion."""
        adk_request = await self._convert_to_adk_request(request)

        # 获取对应的后端地址
        backend_url = self.get_backend_url(adk_request._mapping_key)

        # Ensure session exists before running
        # 使用 agent_name 作为 ADK API 的 appName
        await self._ensure_session(adk_request._agent_name, adk_request.userId, adk_request.sessionId, backend_url)

        # Log the request for debugging
        request_data = adk_request.to_adk_format()
        logger.info(f"Sending ADK request to {backend_url}/run (app={adk_request.appName})")
        logger.debug(f"Request data: {request_data}")

        try:
            client = await self._get_client()
            response = await client.post(
                f"{backend_url}/run",
                json=request_data
            )
            logger.info(f"ADK response status: {response.status_code}")

            if response.status_code == 200:
                adk_response = response.json()
                return self._convert_from_adk_response(adk_response, request.model)

            # Handle error response
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
        adk_request.streaming = True  # Enable ADK streaming

        # 获取对应的后端地址
        backend_url = self.get_backend_url(adk_request._mapping_key)

        # Ensure session exists before running
        # 使用 agent_name 作为 ADK API 的 appName
        await self._ensure_session(adk_request._agent_name, adk_request.userId, adk_request.sessionId, backend_url)

        # Log the request for debugging
        request_data = adk_request.to_adk_format()
        logger.info(f"Sending ADK request to {backend_url}/run_sse (real streaming, app={adk_request.appName})")

        # Track sent content for deduplication
        sent_content_tracker = {}
        tracker_key = f"{request.user or 'default'}:{int(time.time())}"
        sent_content_tracker[tracker_key] = ""

        try:
            client = await self._get_client()
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
                            # Send final chunk
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
                                # Update tracker with new content
                                if "choices" in chunk and chunk["choices"]:
                                    delta = chunk["choices"][0].get("delta", {})
                                    new_content = delta.get("content", "")
                                    if new_content:
                                        sent_content_tracker[tracker_key] += new_content

                                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse SSE data: {data_str[:100]}")
                            continue

                # If we get here without [DONE], send final chunk anyway
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
        Handles deduplication by tracking previously sent content.
        """
        try:
            # Extract content from ADK event
            content = ""

            # Try different event structures
            if "content" in adk_event:
                content_part = adk_event["content"]
                if isinstance(content_part, dict) and "parts" in content_part:
                    for part in content_part["parts"]:
                        if "text" in part:
                            content += part["text"]
            elif "text" in adk_event:
                content = adk_event["text"]
            elif "data" in adk_event and isinstance(adk_event["data"], str):
                content = adk_event["data"]

            if not content:
                return None

            # Deduplication: only send new content
            new_content = self._extract_new_content(content, previously_sent)

            if not new_content:
                return None

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
            return None

    def _extract_new_content(self, current_content: str, previously_sent: str) -> str:
        """
        Extract only new content that hasn't been sent yet.
        Handles incremental updates and full content updates.
        """
        if not previously_sent:
            return current_content

        # If current is exactly the same as previous, nothing new
        if current_content == previously_sent:
            return ""

        # If current starts with previous, it's an incremental update
        if current_content.startswith(previously_sent):
            return current_content[len(previously_sent):]

        # If current is shorter, might be a fragment or reset
        # Check for overlap
        max_overlap = 0
        for i in range(1, min(len(previously_sent), len(current_content)) + 1):
            if previously_sent[-i:] == current_content[:i]:
                max_overlap = i

        if max_overlap > 0:
            return current_content[max_overlap:]

        # No clear relationship, send full content (might be a reset)
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
        """
        从 ADK 后端获取应用列表

        Args:
            backend_url: 后端地址

        Returns:
            应用列表，格式: [{"name": "app1", "agents": ["agent1", ...]}, ...]
            如果后端不可达，返回空列表
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{backend_url}/list-apps")
            response.raise_for_status()
            data = response.json()
            logger.info(f"Got apps list from {backend_url}/list-apps: {data}")
            return data
        except Exception as e:
            logger.warning(f"Failed to get apps list from {backend_url}: {e}")
            return []

    async def list_models(self, request_model: str = None) -> ListModelsResponse:
        """
        List available models (ADK agents).

        仅从可访问的后端获取模型列表。如果后端不可达，不返回该后端的模型。

        返回格式: {mapping_key}/{agent_name}
        例如: data-analysis/my_agent, data-analysis/other_agent

        Args:
            request_model: 请求中的 model 字段（可选），用于确定从哪个后端获取
        """
        models = []
        seen_models = set()  # 去重

        # 确定要查询的后端
        backends_to_query = {}

        if request_model and "/" in request_model:
            # 如果指定了 model，只查询对应的后端
            mapping_key, _ = settings.parse_model(request_model)
            backend_url = self.get_backend_url(mapping_key)
            backends_to_query[mapping_key] = backend_url
        else:
            # 否则，查询所有配置的后端
            backends_to_query = get_backend_manager().get_all_enabled_backends().copy()

        # 从每个后端获取模型
        for mapping_key, backend_url in backends_to_query.items():
            try:
                apps_data = await self.list_apps(backend_url)

                if isinstance(apps_data, list):
                    # 处理返回的数据
                    # 期望格式: [{"name": "my_agent"}, {"name": "other_agent"}]
                    # 或者: ["my_agent", "other_agent"]
                    for item in apps_data:
                        if isinstance(item, dict):
                            # 字典格式，提取 name 字段作为 agent_name
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
                            # 字符串格式，直接作为 agent_name
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
                # 继续尝试其他后端

        return ListModelsResponse(data=models)
    
    async def _convert_to_adk_request(self, request: ChatCompletionRequest) -> ADKRunRequest:
        """Convert OpenAI request to ADK request format."""
        # 解析 model 字段，提取 app_name 和 agent_name
        if not request.model:
            raise ValueError(
                "Model is required. Must use 'app_name/agent_name' format. "
                f"Available apps: {get_backend_manager().get_all_enabled_keys()}"
            )

        app_name, agent_name = settings.parse_model(request.model)
        logger.info(f"Parsed model: app={app_name}, agent={agent_name}")

        # Extract the last user message (ADK is stateful)
        last_message = request.messages[-1] if request.messages else None

        if not last_message or last_message.role != "user":
            raise ValueError("Last message must be from user")

        # Generate session ID from user field and agent
        user_id = request.user or "anonymous"
        session_id = f"session_{user_id}"

        # Process content (handle multimodal)
        if isinstance(last_message.content, str):
            # Simple text content
            logger.info(f"Processing simple text content: {last_message.content[:100]}...")
            adk_parts = [ADKPart(text=last_message.content)]
        else:
            # Multimodal content
            logger.info(f"Processing multimodal content with {len(last_message.content)} parts")
            for i, part in enumerate(last_message.content):
                logger.info(f"Part {i}: type={part.type}, content={str(part)[:100]}...")

            _, adk_parts = await self.multimodal_processor.process_content(last_message.content)
            logger.info(f"Processed into {len(adk_parts)} ADK parts")
            for i, part in enumerate(adk_parts):
                if part.text:
                    logger.info(f"ADK Part {i}: text={part.text[:100]}...")
                elif part.inlineData:
                    logger.info(f"ADK Part {i}: inlineData mimeType={part.inlineData.mimeType}, dataLength={len(part.inlineData.data)}")
                else:
                    logger.info(f"ADK Part {i}: {part}")

        # Create ADK message
        adk_message = ADKMessage(
            role="user",
            parts=adk_parts
        )

        # Create ADK request
        # appName: 使用 agent_name（ADK 提供的真实名字，如 my_agent）
        # app_name (mapping key) 只用于路由到正确的后端
        adk_request = ADKRunRequest(
            appName=agent_name,  # 使用 ADK agent 名，不是 mapping key
            userId=user_id,
            sessionId=session_id,
            streaming=request.stream,
            newMessage=adk_message
        )

        # 存储原始 model 字符串用于响应
        adk_request._original_model = request.model
        adk_request._mapping_key = app_name  # mapping key 用于路由
        adk_request._agent_name = agent_name  # agent_name 用于 ADK 请求

        return adk_request
    
    def _convert_from_adk_response(self, adk_response, model: str) -> ChatCompletionResponse:
        """Convert ADK response to OpenAI response format."""
        logger.info(f"Converting ADK response of type: {type(adk_response)}")
        
        # Handle list response (ADK may return a list of responses)
        if isinstance(adk_response, list):
            logger.info(f"ADK returned list with {len(adk_response)} items")
            if not adk_response:
                content = ""
            else:
                # Take the last response from the list (most complete)
                last_response = adk_response[-1]
                logger.info(f"Using last response: {type(last_response)}")
                return self._convert_from_adk_response(last_response, model)
        elif not isinstance(adk_response, dict):
            logger.error(f"Unexpected ADK response type: {type(adk_response)}")
            content = str(adk_response)
        else:
            # Extract text content from ADK response
            content = ""
            if "content" in adk_response and "parts" in adk_response["content"]:
                for part in adk_response["content"]["parts"]:
                    if "text" in part:
                        content += part["text"]
            else:
                # Try to extract content from other possible structures
                logger.warning(f"ADK response structure: {list(adk_response.keys()) if isinstance(adk_response, dict) else 'Not a dict'}")
                # Fallback: try to find any text content
                if isinstance(adk_response, dict):
                    for key, value in adk_response.items():
                        if isinstance(value, str) and len(value) > 10:
                            content = value
                            logger.info(f"Using content from key '{key}': {content[:100]}...")
                            break
        
        logger.info(f"Final extracted content: {content[:100]}... (length: {len(content)})")
        
        # Create OpenAI response
        response = ChatCompletionResponse(
            id=f"chatcmpl-{int(time.time())}",
            created=int(time.time()),
            model=model,
            choices=[
                ChatCompletionResponseChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=content),
                    finish_reason="stop"
                )
            ]
        )
        
        return response
    
    def _create_event_fingerprint(self, adk_event: dict) -> str:
        """Create a unique fingerprint for an ADK event to detect duplicates."""
        try:
            # Use event ID if available
            if "id" in adk_event and adk_event["id"]:
                return f"id:{adk_event['id']}"
            
            # Otherwise use content hash
            content = ""
            if "content" in adk_event and "parts" in adk_event["content"]:
                for part in adk_event["content"]["parts"]:
                    if "text" in part:
                        content += part["text"]
            
            if content:
                # Use hash of content for fingerprint
                import hashlib
                return f"content:{hashlib.md5(content.encode()).hexdigest()}"
            
            # Fallback to full event hash
            import hashlib
            event_str = json.dumps(adk_event, sort_keys=True)
            return f"event:{hashlib.md5(event_str.encode()).hexdigest()}"
            
        except Exception:
            # Last resort: use string representation
            return str(adk_event)

    def _has_significant_overlap(self, content1: str, content2: str, min_overlap: int = 10) -> bool:
        """Check if two content strings have significant overlap."""
        if not content1 or not content2:
            return False
        
        # Find the longest common substring
        max_overlap = 0
        len1, len2 = len(content1), len(content2)
        
        # Check for overlap at different positions
        for i in range(min(len1, len2)):
            if content1[:i] == content2[-i:]:
                max_overlap = max(max_overlap, i)
            if content1[-i:] == content2[:i]:
                max_overlap = max(max_overlap, i)
        
        return max_overlap >= min_overlap
    
    def _extract_new_content(self, current: str, previous: str) -> str:
        """Extract only the new part of content when there's overlap."""
        if not previous:
            return current
        
        # Try to find where previous content appears in current
        if previous in current:
            return current[len(previous):]
        
        # Try to find overlap at the end
        max_overlap = 0
        overlap_pos = 0
        len_prev, len_curr = len(previous), len(current)
        
        for i in range(1, min(len_prev, len_curr) + 1):
            if previous[-i:] == current[:i]:
                max_overlap = i
                overlap_pos = i
        
        if max_overlap > 0:
            return current[overlap_pos:]
        
        # No clear overlap, return current
        return current

    def _extract_content_key(self, adk_event: dict) -> str:
        """Extract a unique key from ADK event based on content."""
        try:
            content = ""
            if "content" in adk_event and "parts" in adk_event["content"]:
                for part in adk_event["content"]["parts"]:
                    if "text" in part:
                        content += part["text"]
            return content
        except Exception:
            return str(adk_event)
    
    def _convert_adk_event_to_openai_chunk(self, adk_event: dict, model: str, request_key: str) -> Optional[str]:
        """Convert ADK SSE event to OpenAI chunk format."""
        try:
            # Extract content from ADK event
            content = ""
            if "content" in adk_event and "parts" in adk_event["content"]:
                for part in adk_event["content"]["parts"]:
                    if "text" in part:
                        content += part["text"]
            
            if not content:
                return None
            
            # Log content for debugging
            logger.info(f"EXTRACTED CONTENT: {content[:100]}... (length: {len(content)})")
            
            # Get previous content for this request
            previous_content = self._content_cache.get(request_key, "")
            
            logger.info(f"PREVIOUS CONTENT: {previous_content[:100]}... (length: {len(previous_content)})")
            logger.info(f"CONTENT COMPARISON: current==previous? {content == previous_content}")
            
            # Simple and robust deduplication logic
            new_content = ""
            
            if not previous_content:
                # First message, send full content
                new_content = content
                logger.info(f"FIRST MESSAGE: sending {len(content)} chars")
            elif content == previous_content:
                # Exact duplicate, skip entirely
                logger.warning(f"*** DUPLICATE - SKIPPING {len(content)} chars ***")
                return None
            elif content.startswith(previous_content):
                # Normal extension, send only the new part
                new_content = content[len(previous_content):]
                logger.info(f"EXTENSION: sending {len(new_content)} new chars (total: {len(content)})")
            elif len(content) < len(previous_content) * 0.8:
                # Likely a fragment or old message, skip
                logger.warning(f"*** FRAGMENT/OLD - SKIPPING {len(content)} chars (previous: {len(previous_content)}) ***")
                return None
            else:
                # Content reset or different format, send full content
                new_content = content
                logger.warning(f"*** RESET - sending full content {len(content)} chars ***")
            
            # Update cache with current complete content
            self._content_cache[request_key] = content
            
            if not new_content.strip():
                # No new content to send
                return None
            
            # Create OpenAI chunk with only the new content
            chunk = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": new_content},
                        "finish_reason": None
                    }
                ]
            }
            
            return json.dumps(chunk, ensure_ascii=False)
            
        except Exception as e:
            logger.error(f"Error converting ADK event to OpenAI chunk: {e}")
            return None
    
    async def _ensure_session(self, agent_name: str, user_id: str, session_id: str, backend_url: str = None):
        """
        Ensure session exists before running agent.

        Args:
            agent_name: ADK agent 名（用于 ADK API 路径）
            user_id: 用户 ID
            session_id: 会话 ID
            backend_url: 后端 URL（可选）
        """
        session_key = f"{agent_name}:{user_id}:{session_id}"

        if session_key in self._session_cache:
            logger.debug(f"Session already in cache: {session_key}")
            return

        if backend_url is None:
            # 如果没有提供 backend_url，无法创建会话
            raise ValueError("backend_url must be provided when calling _ensure_session")

        try:
            client = await self._get_client()
            # Create session using ADK API
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
                # Session already exists
                logger.info(f"ADK session already exists: {session_id}")
                self._session_cache.add(session_key)
            else:
                logger.warning(f"Failed to create ADK session: {response.status_code} - {response.text}")

        except Exception as e:
            logger.error(f"Error ensuring ADK session: {e}")
            # Don't raise here, let the main request continue

    async def _delete_session(self, agent_name: str, user_id: str, session_id: str, backend_url: str = None) -> bool:
        """
        Delete an ADK session.

        Args:
            agent_name: ADK agent 名（用于 ADK API 路径）
            user_id: 用户 ID
            session_id: 会话 ID
            backend_url: 后端 URL（可选）
        """
        session_key = f"{agent_name}:{user_id}:{session_id}"

        try:
            client = await self._get_client()
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
        """
        Reset a corrupted session by deleting and recreating it.

        Args:
            agent_name: ADK agent 名（用于 ADK API 路径）
            user_id: 用户 ID
            session_id: 会话 ID
            backend_url: 后端 URL
        """
        session_key = f"{agent_name}:{user_id}:{session_id}"

        logger.warning(f"Resetting corrupted session: {session_id}")

        # Delete the session
        await self._delete_session(agent_name, user_id, session_id, backend_url)

        # Clear from cache so it will be recreated
        self._session_cache.discard(session_key)

        # Create new session
        await self._ensure_session(agent_name, user_id, session_id, backend_url)

    # ============ Public API Methods ============

    async def check_health(self) -> dict:
        """
        Check health status of all configured ADK backends.

        Returns dict with overall status and per-backend details.
        """
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
        """
        Delete a specific session.

        Args:
            agent_name: ADK agent 名
            user_id: 用户 ID
            session_id: 会话 ID
            mapping_key: 映射 key（用于获取 backend_url）

        Returns:
            结果字典
        """
        if mapping_key is None:
            mapping_key = agent_name  # 假设 agent_name 就是 mapping_key
        backend_url = self.get_backend_url(mapping_key)
        success = await self._delete_session(agent_name, user_id, session_id, backend_url)
        return {
            "success": success,
            "session_id": session_id,
            "agent_name": agent_name,
            "user_id": user_id
        }

    async def reset_session(self, agent_name: str, user_id: str, session_id: str, mapping_key: str = None) -> dict:
        """
        Reset a session by deleting and recreating it.

        Args:
            agent_name: ADK agent 名
            user_id: 用户 ID
            session_id: 会话 ID
            mapping_key: 映射 key（用于获取 backend_url）

        Returns:
            结果字典
        """
        if mapping_key is None:
            mapping_key = agent_name  # 假设 agent_name 就是 mapping_key
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
        """
        List all sessions in local cache.

        Returns:
            会话列表，每个会话包含 agent_name, user_id, session_id
        """
        sessions = []
        for session_key in self._session_cache:
            parts = session_key.split(":")
            if len(parts) == 3:
                sessions.append({
                    "agent_name": parts[0],  # ADK agent 名
                    "user_id": parts[1],
                    "session_id": parts[2]
                })
        return sessions
        """Check if an error is recoverable by resetting the session."""
        # Client errors (4xx) might be due to corrupted session state
        # Especially 400 Bad Request with multimodal content issues
        if status_code == 400:
            return True
        # Some 500 errors might also be recoverable
        if status_code >= 500:
            return True
        return False