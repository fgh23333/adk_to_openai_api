from pydantic import BaseModel
from typing import List, Optional, Union, Literal, Any
from enum import Enum


class MessageType(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ContentPartType(str, Enum):
    TEXT = "text"
    IMAGE_URL = "image_url"
    AUDIO_URL = "audio_url"
    VIDEO_URL = "video_url"
    INPUT_AUDIO = "input_audio"
    FILE = "file"


class ImageUrl(BaseModel):
    url: str


class AudioUrl(BaseModel):
    url: str


class VideoUrl(BaseModel):
    url: str


class InputAudio(BaseModel):
    data: str  # Base64 encoded audio data
    format: str  # e.g., "mp3", "wav", "flac", "aac"


class FileContent(BaseModel):
    url: Optional[str] = None
    data: Optional[str] = None  # Base64 encoded file data
    filename: Optional[str] = None
    mime_type: Optional[str] = None


class ContentPart(BaseModel):
    type: ContentPartType
    text: Optional[str] = None
    image_url: Optional[ImageUrl] = None
    audio_url: Optional[AudioUrl] = None
    video_url: Optional[VideoUrl] = None
    input_audio: Optional[InputAudio] = None
    file: Optional[FileContent] = None


class ChatMessage(BaseModel):
    role: MessageType
    content: Union[str, List[ContentPart]]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: bool = False
    user: Optional[str] = None
    temperature: Optional[float] = 1.0


class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]


class ChatCompletionStreamDelta(BaseModel):
    content: Optional[str] = None


class ChatCompletionStreamChoice(BaseModel):
    index: int
    delta: ChatCompletionStreamDelta
    finish_reason: Optional[str] = None


class ChatCompletionStreamResponse(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatCompletionStreamChoice]


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str


class ListModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]


class ErrorResponse(BaseModel):
    error: dict


# ADK Models
class ADKInlineData(BaseModel):
    mimeType: str
    data: str


class ADKPart(BaseModel):
    text: Optional[str] = None
    inlineData: Optional[ADKInlineData] = None


class ADKMessage(BaseModel):
    role: str
    parts: List[ADKPart]


class ADKRunRequest(BaseModel):
    appName: str
    userId: str
    sessionId: str
    streaming: bool = False
    newMessage: ADKMessage
    
    def to_adk_format(self) -> dict:
        """Convert to ADK API format."""
        return {
            "appName": self.appName,
            "userId": self.userId,
            "sessionId": self.sessionId,
            "streaming": self.streaming,
            "newMessage": {
                "role": self.newMessage.role,
                "parts": [
                    {"text": part.text} if part.text else {"inlineData": part.inlineData.dict()}
                    for part in self.newMessage.parts
                ]
            }
        }


class ADKContentPart(BaseModel):
    text: Optional[str] = None


class ADKContent(BaseModel):
    parts: List[ADKContentPart]


class ADKEvent(BaseModel):
    event: str
    data: Optional[dict] = None
    content: Optional[ADKContent] = None


class HealthResponse(BaseModel):
    status: str = "ok"