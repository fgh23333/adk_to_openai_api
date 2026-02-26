from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List, Dict


class Settings(BaseSettings):
    # ADK Backend Configuration
    adk_host: str = "http://localhost:8000"  # 默认后端地址
    adk_app_name: str = "agent"  # 默认应用名

    # ADK 后端地址映射表 (应用名 -> 后端地址)
    # 格式: app1:backend1,app2:backend2 或 JSON 格式
    adk_backend_mapping_str: str = Field(default="", alias="ADK_BACKEND_MAPPING")

    # Middleware Server Configuration
    port: int = 8080
    log_level: str = "INFO"

    # File Processing Limits
    max_file_size_mb: int = 20
    download_timeout: int = 30

    # API Key Configuration
    require_api_key: bool = False
    api_keys_str: str = Field(default="", alias="API_KEYS")
    default_api_key: str = "sk-adk-middleware-key"

    # Database Configuration
    database_path: str = "data/sessions.db"
    session_history_enabled: bool = True

    @property
    def api_keys(self) -> List[str]:
        """解析 API_KEYS 字符串为列表"""
        if self.api_keys_str:
            return [key.strip() for key in self.api_keys_str.split(",") if key.strip()]
        return [self.default_api_key]

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


settings = Settings()