from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List, Dict


class Settings(BaseSettings):
    # ADK Backend Configuration
    adk_host: str = "http://localhost:8000"  # 默认后端地址
    adk_app_name: str = "agent"  # 默认应用名
    adk_backend_mapping_str: str = Field(default="", alias="ADK_BACKEND_MAPPING")
    adk_timeout: int = 120000
    adk_connect_timeout: int = 30000

    # Middleware Server Configuration
    port: int = 8000
    log_level: str = "INFO"

    # Docker/Deployment Configuration (for docker-compose)
    container_name: str = "adk-middleware"
    host_port: int = 8000
    host_data_path: str = "./data"
    url_prefix: str = "/adk"
    project_name: str = "adk-middleware"

    # File Processing Limits
    max_file_size_mb: int = 20
    allowed_mime_types: str = "image/*,audio/*,video/*,application/pdf"
    file_download_timeout: int = 60
    max_concurrent_downloads: int = 10

    # API Key Configuration
    enable_api_key_auth: bool = False
    api_key: str = ""

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

        支持格式:
        - app_name/agent_name (推荐，明确指定应用和agent)
        - agent_name (使用默认应用)

        Args:
            model: model 字符串

        Returns:
            (app_name, agent_name) 元组
        """
        if "/" in model:
            # app_name/agent_name 格式
            parts = model.split("/", 1)
            return parts[0], parts[1]
        # 只有 agent_name，使用默认应用
        return self.adk_app_name, model

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
            后端地址，如果未配置映射则返回默认地址
        """
        return self.adk_backend_mapping.get(app_name, self.adk_host)

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 忽略额外的环境变量


settings = Settings()
