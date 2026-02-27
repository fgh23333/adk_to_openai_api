"""
管理员接口路由 - API Key 管理和系统管理
"""
import logging
from fastapi import APIRouter, Request
from app.core.api_key_manager import get_api_key_manager
from app.database.database import get_database
from app.core.config import settings
from app.core.auth import verify_api_key_dependency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin")

# Initialize database
db = None


@router.post("/api-keys")
async def add_api_key(
    request: Request,
    api_key: str,
    metadata: dict = None,
    api_key_valid: str = None
) -> dict:
    """动态添加 API Key（管理员接口）"""
    manager = get_api_key_manager()
    success = manager.add_key(api_key, metadata)

    return {
        "success": success,
        "message": "API key added successfully" if success else "API key already exists",
        "key_prefix": api_key[:10] + "..."
    }


@router.delete("/api-keys/{api_key}")
async def remove_api_key(
    api_key: str,
    api_key_valid: str = None
) -> dict:
    """删除 API Key（管理员接口）"""
    manager = get_api_key_manager()
    success = manager.remove_key(api_key)

    return {
        "success": success,
        "message": "API key removed successfully" if success else "API key not found"
    }


@router.get("/api-keys")
async def list_api_keys(
    include_value: bool = False,
    api_key_valid: str = None
) -> dict:
    """列出所有 API Keys（管理员接口）"""
    manager = get_api_key_manager()
    keys = manager.list_keys(include_value=include_value)

    return {
        "count": len(keys),
        "keys": keys
    }


@router.post("/api-keys/reload")
async def reload_api_keys(
    api_key_valid: str = None
) -> dict:
    """从环境变量重新加载 API Keys（管理员接口）"""
    manager = get_api_key_manager()
    manager.reload_from_env()

    keys = manager.list_keys(include_value=False)

    return {
        "success": True,
        "message": "API keys reloaded from environment",
        "count": len(keys)
    }


@router.post("/api-keys/export")
async def export_api_keys(
    filepath: str = "data/api_keys_backup.json",
    api_key_valid: str = None
) -> dict:
    """导出 API Keys 到文件（管理员接口）"""
    manager = get_api_key_manager()
    success = manager.export_to_file(filepath)

    return {
        "success": success,
        "message": f"API keys exported to {filepath}" if success else "Export failed"
    }
