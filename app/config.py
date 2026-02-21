from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List


class Settings(BaseSettings):
    # ADK Backend Configuration
    adk_host: str = "http://localhost:8000"
    adk_app_name: str = "agent"

    # Middleware Server Configuration
    port: int = 8080
    log_level: str = "INFO"

    # File Processing Limits
    max_file_size_mb: int = 20
    download_timeout: int = 30

    # API Key Configuration
    require_api_key: bool = False  # 是否需要 API Key 验证
    api_keys_str: str = Field(default="", alias="API_KEYS")  # 从环境变量读取的字符串
    default_api_key: str = "sk-adk-middleware-key"  # 默认 API Key

    # Database Configuration
    database_path: str = "data/sessions.db"
    session_history_enabled: bool = True  # 是否启用会话记录

    @property
    def api_keys(self) -> List[str]:
        """解析 API_KEYS 字符串为列表"""
        if self.api_keys_str:
            return [key.strip() for key in self.api_keys_str.split(",") if key.strip()]
        return [self.default_api_key]

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()