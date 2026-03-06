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

# ==================== Admin API Models ====================

from typing import Any
from pydantic import BaseModel, Field


class APIKeyAddRequest(BaseModel):
    """添加 API Key 请求"""
    api_key: str = Field(..., description="API Key 字符串", min_length=10)
    metadata: Optional[dict] = Field(default=None, description="可选元数据")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
                    "metadata": {"user": "test-user", "description": "测试用 API Key"}
                }
            ]
        }
    }


class APIKeyResponse(BaseModel):
    """API Key 操作响应"""
    success: bool
    message: str
    key_prefix: Optional[str] = None


class APIKeyListResponse(BaseModel):
    """API Key 列表响应"""
    count: int
    keys: list


class BackendAddRequest(BaseModel):
    """添加后端请求"""
    mapping_key: str = Field(..., description="路由标识（如 data-analysis）", min_length=1)
    url: str = Field(..., description="后端地址（如 http://172.31.243.14:8000）")
    description: str = Field(default="", description="可选描述")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "mapping_key": "data-analysis",
                    "url": "http://172.31.243.14:8000",
                    "description": "数据分析后端"
                }
            ]
        }
    }


class BackendUpdateRequest(BaseModel):
    """更新后端请求"""
    url: Optional[str] = Field(default=None, description="新的后端地址")
    description: Optional[str] = Field(default=None, description="新的描述")
    enabled: Optional[bool] = Field(default=None, description="是否启用")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "http://new-backend:8000",
                    "description": "更新后的描述",
                    "enabled": True
                }
            ]
        }
    }


class BackendResponse(BaseModel):
    """后端操作响应"""
    success: bool
    message: str
    mapping_key: Optional[str] = None
    url: Optional[str] = None


class BackendInfo(BaseModel):
    """后端信息"""
    mapping_key: str
    url: str
    enabled: bool
    added_at: str
    source: str
    description: str = ""
    updated_at: Optional[str] = None


class BackendListResponse(BaseModel):
    """后端列表响应"""
    count: int
    backends: list


class BackendImportRequest(BaseModel):
    """导入后端请求"""
    filepath: str = Field(..., description="JSON 文件路径")
    replace: bool = Field(default=False, description="是否替换现有配置")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "filepath": "data/backends_backup.json",
                    "replace": False
                }
            ]
        }
    }


class BackendImportResponse(BaseModel):
    """导入后端响应"""
    success: bool
    message: str
    imported_count: int


class ExportRequest(BaseModel):
    """导出请求"""
    filepath: str = Field(default="data/backup.json", description="导出文件路径")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "filepath": "data/backup.json"
                }
            ]
        }
    }


class ExportResponse(BaseModel):
    """导出响应"""
    success: bool
    message: str
