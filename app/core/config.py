from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List, Dict
from contextvars import ContextVar


# Context variable for request ID
request_id_var: ContextVar[str] = ContextVar('request_id', default='')


def get_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    try:
        return request_id_var.get()
    except LookupError:
        return None


def set_request_id(request_id: str):
    """Set the request ID in context."""
    request_id_var.set(request_id)


class Settings(BaseSettings):
    # ADK Backend Configuration
    adk_backend_mapping_str: str = Field(default="", alias="ADK_BACKEND_MAPPING")
    adk_timeout: int = 120000
    adk_connect_timeout: int = 30000

    # Server Configuration
    port: int = 8000
    log_level: str = "INFO"

    # File Processing Limits
    max_file_size_mb: int = 20
    file_download_timeout: int = 60
    max_concurrent_downloads: int = 10

    # API Key Configuration
    enable_api_key_auth: bool = False
    api_keys_str: str = Field(default="", alias="API_KEYS")
    default_api_key: str = "sk-adk-middleware-key"

    @property
    def api_keys(self) -> List[str]:
        """解析 API_KEYS 字符串为列表"""
        if self.api_keys_str:
            return [key.strip() for key in self.api_keys_str.split(",") if key.strip()]
        return [self.default_api_key]

    # Database Configuration
    database_path: str = "data/sessions.db"
    session_history_enabled: bool = True
    session_retention_days: int = 30

    # Monitoring Configuration
    enable_metrics: bool = True
    metrics_retention_hours: int = 24

    # CORS Configuration
    allowed_origins: str = "*"

    @property
    def adk_backend_mapping(self) -> Dict[str, str]:
        """
        解析 ADK 后端地址映射表

        支持两种格式:
        1. 简单格式: app1:http://backend1,app2:http://backend2
        2. JSON 格式: {"app1": "http://backend1", "app2": "http://backend2"}
        """
        if not self.adk_backend_mapping_str:
            return {}

        # 尝试 JSON 格式
        if self.adk_backend_mapping_str.strip().startswith("{"):
            try:
                import json
                return json.loads(self.adk_backend_mapping_str)
            except json.JSONDecodeError:
                pass

        # 简单格式: app1:url1,app2:url2
        mapping = {}
        for pair in self.adk_backend_mapping_str.split(","):
            if ":" in pair:
                app_name, url = pair.split(":", 1)
                mapping[app_name.strip()] = url.strip()

        return mapping

    def parse_model(self, model: str) -> tuple[str, str]:
        """
        解析 model 字符串，提取 app_name 和 agent_name

        必须使用 app_name/agent_name 格式

        Args:
            model: model 字符串 (格式: app_name/agent_name)

        Returns:
            (app_name, agent_name) 元组

        Raises:
            ValueError: 如果 model 格式不正确
        """
        if "/" not in model:
            raise ValueError(
                f"Invalid model format '{model}'. "
                f"Must use 'app_name/agent_name' format. "
                f"Available apps: {list(self.adk_backend_mapping.keys())}"
            )

        parts = model.split("/", 1)
        return parts[0], parts[1]

    def format_model(self, app_name: str, agent_name: str) -> str:
        """
        格式化 model 字符串，返回给前端

        Args:
            app_name: 应用名
            agent_name: agent 名

        Returns:
            格式化后的 model 字符串 (app_name/agent_name)
        """
        return f"{app_name}/{agent_name}"

    def get_backend_url(self, app_name: str) -> str:
        """
        根据应用名获取对应的后端地址

        Args:
            app_name: ADK 应用名

        Returns:
            后端地址

        Raises:
            ValueError: 如果应用未在映射中配置
        """
        if app_name not in self.adk_backend_mapping:
            raise ValueError(
                f"Application '{app_name}' not configured in ADK_BACKEND_MAPPING. "
                f"Available apps: {list(self.adk_backend_mapping.keys())}"
            )
        return self.adk_backend_mapping[app_name]

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 忽略额外的环境变量


settings = Settings()
