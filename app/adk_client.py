import json
import time
from typing import AsyncGenerator, Optional
import httpx
from app.config import settings
from app.models import (
    ChatCompletionRequest, ChatCompletionResponse, ChatCompletionResponseChoice,
    ChatMessage, ADKRunRequest, ADKMessage, ADKPart, ListModelsResponse, ModelInfo
)
from app.multimodal import MultimodalProcessor
import logging

logger = logging.getLogger(__name__)


class ADKClient:
    def __init__(self):
        self.adk_host = settings.adk_host
        self.default_app_name = settings.adk_app_name
        self.multimodal_processor = MultimodalProcessor()
        self._session_cache = set()  # Simple cache for created sessions
        self._content_cache = {}  # Cache to track sent content for deduplication
        self._event_cache = set()  # Cache to track processed events for deduplication
        
    async def create_chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Create a non-streaming chat completion."""
        adk_request = await self._convert_to_adk_request(request)

        # Ensure session exists before running
        await self._ensure_session(adk_request.appName, adk_request.userId, adk_request.sessionId)

        # Log the request for debugging
        request_data = adk_request.to_adk_format()
        logger.info(f"Sending ADK request to {self.adk_host}/run")
        logger.debug(f"Request data: {request_data}")

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.adk_host}/run",
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

        # Ensure session exists before running
        await self._ensure_session(adk_request.appName, adk_request.userId, adk_request.sessionId)

        # Log the request for debugging
        request_data = adk_request.to_adk_format()
        logger.info(f"Sending ADK request to {self.adk_host}/run_sse (real streaming)")

        # Track sent content for deduplication
        sent_content_tracker = {}
        tracker_key = f"{request.user or 'default'}:{int(time.time())}"
        sent_content_tracker[tracker_key] = ""

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.adk_host}/run_sse",
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

    async def list_models(self) -> ListModelsResponse:
        """List available models (ADK agents)."""
        # For now, return a default model. In a real implementation, 
        # you might want to query ADK for available agents.
        model = ModelInfo(
            id=self.default_app_name,
            created=int(time.time()),
            owned_by="adk"
        )
        
        return ListModelsResponse(data=[model])
    
    async def _convert_to_adk_request(self, request: ChatCompletionRequest) -> ADKRunRequest:
        """Convert OpenAI request to ADK request format."""
        # Extract the last user message (ADK is stateful)
        last_message = request.messages[-1] if request.messages else None
        
        if not last_message or last_message.role != "user":
            raise ValueError("Last message must be from user")
        
        # Generate session ID from user field
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
        
        # Create ADK request - try different possible formats
        adk_request = ADKRunRequest(
            appName=request.model or self.default_app_name,
            userId=user_id,
            sessionId=session_id,
            streaming=request.stream,
            newMessage=adk_message
        )
        
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
    
    async def _ensure_session(self, app_name: str, user_id: str, session_id: str):
        """Ensure session exists before running agent."""
        session_key = f"{app_name}:{user_id}:{session_id}"

        if session_key in self._session_cache:
            logger.debug(f"Session already in cache: {session_key}")
            return

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Create session using ADK API
                logger.info(f"Creating ADK session: {session_id} for app={app_name}, user={user_id}")
                response = await client.post(
                    f"{self.adk_host}/apps/{app_name}/users/{user_id}/sessions",
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

    async def _delete_session(self, app_name: str, user_id: str, session_id: str) -> bool:
        """Delete an ADK session."""
        session_key = f"{app_name}:{user_id}:{session_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"Deleting ADK session: {session_id}")
                response = await client.delete(
                    f"{self.adk_host}/apps/{app_name}/users/{user_id}/sessions/{session_id}"
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

    async def _reset_session(self, app_name: str, user_id: str, session_id: str):
        """Reset a corrupted session by deleting and recreating it."""
        logger.warning(f"Resetting corrupted session: {session_id}")

        # Delete the session
        await self._delete_session(app_name, user_id, session_id)

        # Clear from cache so it will be recreated
        session_key = f"{app_name}:{user_id}:{session_id}"
        self._session_cache.discard(session_key)

        # Create new session
        await self._ensure_session(app_name, user_id, session_id)

    # ============ Public API Methods ============

    async def check_health(self) -> dict:
        """
        Check health status of ADK backend connection.
        Returns dict with status and details.
        """
        result = {
            "middleware": "healthy",
            "adk_backend": "unknown",
            "adk_host": self.adk_host,
            "details": {}
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                start_time = time.time()
                response = await client.get(f"{self.adk_host}/")
                latency = (time.time() - start_time) * 1000  # ms

                if response.status_code < 500:
                    result["adk_backend"] = "healthy"
                    result["details"]["latency_ms"] = round(latency, 2)
                    result["details"]["status_code"] = response.status_code
                else:
                    result["adk_backend"] = "unhealthy"
                    result["details"]["error"] = f"HTTP {response.status_code}"

        except httpx.TimeoutException:
            result["adk_backend"] = "timeout"
            result["details"]["error"] = "Connection timeout"
        except httpx.ConnectError as e:
            result["adk_backend"] = "unreachable"
            result["details"]["error"] = str(e)
        except Exception as e:
            result["adk_backend"] = "error"
            result["details"]["error"] = str(e)

        result["healthy"] = result["adk_backend"] == "healthy"
        return result

    async def delete_session(self, app_name: str, user_id: str, session_id: str) -> dict:
        """
        Delete a specific session.
        Returns result dict.
        """
        success = await self._delete_session(app_name, user_id, session_id)
        return {
            "success": success,
            "session_id": session_id,
            "app_name": app_name,
            "user_id": user_id
        }

    async def reset_session(self, app_name: str, user_id: str, session_id: str) -> dict:
        """
        Reset a session by deleting and recreating it.
        Returns result dict.
        """
        await self._reset_session(app_name, user_id, session_id)
        return {
            "success": True,
            "session_id": session_id,
            "app_name": app_name,
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
                    "app_name": parts[0],
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