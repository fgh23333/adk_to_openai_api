# -*- coding: utf-8 -*-
"""
管理员接口路由 - API Key 管理和 ADK 后端管理
"""
import logging
from fastapi import APIRouter
from app.core.api_key_manager import get_api_key_manager
from app.core.backend_manager import get_backend_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["Admin"])


# ==================== API Key 管理 ====================

@router.get("/api-keys", summary="列出所有 API Keys")
async def list_api_keys():
    manager = get_api_key_manager()
    keys = manager.list_keys(include_value=False)
    return {"count": len(keys), "keys": keys}


@router.post("/api-keys", summary="添加 API Key")
async def add_api_key(api_key: str, metadata: dict = None):
    manager = get_api_key_manager()
    success = manager.add_key(api_key, metadata)
    return {
        "success": success,
        "message": "API key added successfully" if success else "API key already exists",
        "key_prefix": api_key[:10] + "..."
    }


@router.delete("/api-keys/{api_key}", summary="删除 API Key")
async def remove_api_key(api_key: str):
    manager = get_api_key_manager()
    success = manager.remove_key(api_key)
    return {
        "success": success,
        "message": "API key removed successfully" if success else "API key not found"
    }


@router.post("/api-keys/reload", summary="重新加载 API Keys")
async def reload_api_keys():
    manager = get_api_key_manager()
    manager.reload_from_env()
    keys = manager.list_keys(include_value=False)
    return {"success": True, "message": "API keys reloaded from environment", "count": len(keys)}


# ==================== ADK 后端管理 ====================

@router.get("/backends", summary="列出所有 ADK 后端")
async def list_backends(include_disabled: bool = False):
    manager = get_backend_manager()
    backends = manager.list_backends(include_disabled=include_disabled)
    return {"count": len(backends), "backends": backends}


@router.post("/backends", summary="添加 ADK 后端")
async def add_backend(mapping_key: str, url: str, description: str = ""):
    manager = get_backend_manager()
    success = manager.add_backend(mapping_key, url, description)
    return {
        "success": success,
        "message": f"Backend {mapping_key} added successfully" if success else f"Backend {mapping_key} already exists",
        "mapping_key": mapping_key,
        "url": url
    }


@router.put("/backends/{mapping_key}", summary="更新 ADK 后端")
async def update_backend(mapping_key: str, url: str = None, description: str = None, enabled: bool = None):
    manager = get_backend_manager()
    success = manager.update_backend(mapping_key, url, description, enabled)
    return {
        "success": success,
        "message": f"Backend {mapping_key} updated successfully" if success else f"Backend {mapping_key} not found"
    }


@router.delete("/backends/{mapping_key}", summary="删除 ADK 后端")
async def remove_backend(mapping_key: str):
    manager = get_backend_manager()
    success = manager.remove_backend(mapping_key)
    return {
        "success": success,
        "message": f"Backend {mapping_key} removed successfully" if success else f"Backend {mapping_key} not found"
    }


@router.get("/backends/{mapping_key}", summary="获取单个后端详情")
async def get_backend_detail(mapping_key: str):
    manager = get_backend_manager()
    backend = manager.get_backend(mapping_key)
    if backend:
        return {"success": True, "mapping_key": mapping_key, "backend": backend}
    return {"success": False, "message": f"Backend {mapping_key} not found"}


@router.post("/backends/reload", summary="重新加载后端配置")
async def reload_backends():
    manager = get_backend_manager()
    manager.reload_from_env()
    backends = manager.list_backends()
    return {"success": True, "message": "Backends reloaded from environment", "count": len(backends)}


@router.post("/backends/export", summary="导出后端配置")
async def export_backends(filepath: str = "data/backends_backup.json"):
    manager = get_backend_manager()
    success = manager.export_to_file(filepath)
    return {"success": success, "message": f"Backends exported to {filepath}" if success else "Export failed"}


@router.post("/backends/import", summary="导入后端配置")
async def import_backends(filepath: str, replace: bool = False):
    manager = get_backend_manager()
    count = manager.load_from_file(filepath, replace)
    return {"success": count > 0, "message": f"Loaded {count} backends from {filepath}", "imported_count": count}
