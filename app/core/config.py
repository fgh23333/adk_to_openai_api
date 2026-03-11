from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import Optional, List, Dict, Any
from contextvars import ContextVar
import logging
import os


# Context variable for request ID
request_id_var: ContextVar[str] = ContextVar('request_id', default='')

logger = logging.getLogger(__name__)


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

    # Database Configuration
    database_path: str = "data/sessions.db"
    session_history_enabled: bool = True
    session_retention_days: int = 30

    # Monitoring Configuration
    enable_metrics: bool = True
    metrics_retention_hours: int = 24

    # CORS Configuration
    allowed_origins: str = "*"

    # 配置验证标志
    _validated: bool = False

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

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """验证日志级别"""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v_upper = v.upper()
        if v_upper not in valid_levels:
            logger.warning(f"Invalid log level '{v}', defaulting to INFO")
            return 'INFO'
        return v_upper

    @field_validator('port')
    @classmethod
    def validate_port(cls, v: int) -> int:
        """验证端口范围"""
        if not (1 <= v <= 65535):
            logger.warning(f"Invalid port '{v}', defaulting to 8000")
            return 8000
        return v

    @field_validator('max_file_size_mb')
    @classmethod
    def validate_max_file_size(cls, v: int) -> int:
        """验证文件大小限制"""
        if v <= 0:
            logger.warning(f"Invalid max_file_size_mb '{v}', defaulting to 20")
            return 20
        if v > 100:
            logger.warning(f"max_file_size_mb '{v}' is very large, may cause memory issues")
        return v

    def validate_required_config(self) -> List[str]:
        """
        验证必需的配置项

        Returns:
            错误消息列表，空列表表示验证通过
        """
        errors = []

        # 检查后端映射（允许从持久化文件加载，所以这里只是警告）
        if not self.adk_backend_mapping:
            errors.append(
                "ADK_BACKEND_MAPPING is not configured. "
                "Please set it in environment or add backends via admin API."
            )

        # 检查日志级别
        if self.log_level.upper() not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
            errors.append(f"Invalid LOG_LEVEL: {self.log_level}")

        # 检查数据库路径（如果启用会话记录）
        if self.session_history_enabled:
            db_dir = os.path.dirname(self.database_path)
            if db_dir and not os.path.exists(db_dir):
                try:
                    os.makedirs(db_dir, exist_ok=True)
                    logger.info(f"Created database directory: {db_dir}")
                except Exception as e:
                    errors.append(f"Cannot create database directory: {e}")

        # 检查认证配置
        if self.enable_api_key_auth and not self.api_keys:
            errors.append("API Key auth is enabled but no API_KEYS configured")

        return errors

    def validate_on_startup(self) -> bool:
        """
        启动时验证配置

        Returns:
            True if configuration is valid (with possible warnings)
            False if there are critical errors
        """
        if self._validated:
            return True

        logger.info("=" * 50)
        logger.info("Configuration Validation")
        logger.info("=" * 50)

        errors = self.validate_required_config()

        # 打印配置摘要
        logger.info(f"Server Port: {self.port}")
        logger.info(f"Log Level: {self.log_level}")
        logger.info(f"Session History: {'enabled' if self.session_history_enabled else 'disabled'}")
        logger.info(f"API Key Auth: {'enabled' if self.enable_api_key_auth else 'disabled'}")
        logger.info(f"Max File Size: {self.max_file_size_mb}MB")

        if self.adk_backend_mapping:
            logger.info(f"Configured Backends: {list(self.adk_backend_mapping.keys())}")
        else:
            logger.warning("No backends configured in ADK_BACKEND_MAPPING")
            logger.info("You can add backends dynamically via /v1/admin/backends API")

        if errors:
            for error in errors:
                if "ADK_BACKEND_MAPPING" in error:
                    logger.warning(f"Config Warning: {error}")
                else:
                    logger.error(f"Config Error: {error}")

            # 只有 ADK_BACKEND_MAPPING 为空是允许的（可以动态添加）
            critical_errors = [e for e in errors if "ADK_BACKEND_MAPPING" not in e]
            if critical_errors:
                logger.error("Critical configuration errors found!")
                self._validated = True
                return False

        logger.info("=" * 50)
        logger.info("Configuration validation completed")
        logger.info("=" * 50)

        self._validated = True
        return True

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
            app_name: 映射 key（如 data-analysis）

        Returns:
            backend_url

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


# 全局配置实例
_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """获取配置单例"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def reload_settings() -> Settings:
    """重新加载配置（从环境变量）"""
    global _settings_instance
    _settings_instance = Settings()
    logger.info("Settings reloaded from environment")
    return _settings_instance


# 向后兼容
settings = get_settings()
