# -*- coding: utf-8 -*-
"""
管理员接口路由 - API Key 管理和 ADK 后端管理
"""
import logging
import httpx
import time
import asyncio
from fastapi import APIRouter, HTTPException, Query, Body
from typing import Optional

from app.core.api_key_manager import get_api_key_manager
from app.core.backend_manager import get_backend_manager
from app.schemas.models import (
    # API Key models
    APIKeyAddRequest, APIKeyResponse, APIKeyListResponse,
    # Backend models
    BackendAddRequest, BackendUpdateRequest, BackendResponse,
    BackendListResponse, BackendImportRequest, BackendImportResponse,
    ExportRequest, ExportResponse, ReloadResponse,
    # Health models
    AllBackendsHealthResponse, SingleBackendHealthResponse,
    SingleBackendHealthResult, BackendHealthSummary, BackendHealthStatus,
    # Config models
    ConfigResponse, ConfigReloadResponse, ConfigValidationResult,
    ServerConfig, FeaturesConfig, LimitsConfig, DatabaseConfig, BackendsConfig,
    # Common
    SuccessResponse
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["Admin"])


# ==================== API Key 管理 ====================

@router.get("/api-keys", response_model=APIKeyListResponse, summary="列出所有 API Keys")
async def list_api_keys():
    """列出所有已配置的 API Keys（仅显示前缀）"""
    manager = get_api_key_manager()
    keys = manager.list_keys(include_value=False)
    return APIKeyListResponse(count=len(keys), keys=keys)


@router.post("/api-keys", response_model=APIKeyResponse, summary="添加 API Key")
async def add_api_key(request: APIKeyAddRequest = Body(...)):
    """添加新的 API Key"""
    manager = get_api_key_manager()
    success = manager.add_key(request.api_key, request.metadata)
    return APIKeyResponse(
        success=success,
        message="API key added successfully" if success else "API key already exists",
        key_prefix=request.api_key[:10] + "..."
    )


@router.delete("/api-keys/{api_key}", response_model=APIKeyResponse, summary="删除 API Key")
async def remove_api_key(api_key: str):
    """删除指定的 API Key"""
    manager = get_api_key_manager()
    success = manager.remove_key(api_key)
    return APIKeyResponse(
        success=success,
        message="API key removed successfully" if success else "API key not found"
    )


@router.post("/api-keys/reload", response_model=ReloadResponse, summary="重新加载 API Keys")
async def reload_api_keys():
    """从环境变量重新加载 API Keys"""
    manager = get_api_key_manager()
    manager.reload_from_env()
    keys = manager.list_keys(include_value=False)
    return ReloadResponse(
        success=True,
        message="API keys reloaded from environment",
        count=len(keys)
    )


# ==================== ADK 后端管理 ====================

@router.get("/backends", response_model=BackendListResponse, summary="列出所有 ADK 后端")
async def list_backends(include_disabled: bool = Query(default=False, description="是否包含已禁用的后端")):
    """列出所有已配置的 ADK 后端"""
    manager = get_backend_manager()
    backends = manager.list_backends(include_disabled=include_disabled)
    return BackendListResponse(count=len(backends), backends=backends)


@router.post("/backends", response_model=BackendResponse, summary="添加 ADK 后端")
async def add_backend(request: BackendAddRequest = Body(...)):
    """添加新的 ADK 后端"""
    manager = get_backend_manager()
    success = manager.add_backend(request.mapping_key, request.url, request.description)
    return BackendResponse(
        success=success,
        message=f"Backend {request.mapping_key} added successfully" if success else f"Backend {request.mapping_key} already exists",
        mapping_key=request.mapping_key,
        url=request.url
    )


@router.put("/backends/{mapping_key}", response_model=BackendResponse, summary="更新 ADK 后端")
async def update_backend(mapping_key: str, request: BackendUpdateRequest = Body(...)):
    """更新现有 ADK 后端配置"""
    manager = get_backend_manager()
    success = manager.update_backend(
        mapping_key,
        request.url,
        request.description,
        request.enabled
    )
    return BackendResponse(
        success=success,
        message=f"Backend {mapping_key} updated successfully" if success else f"Backend {mapping_key} not found",
        mapping_key=mapping_key
    )


@router.delete("/backends/{mapping_key}", response_model=BackendResponse, summary="删除 ADK 后端")
async def remove_backend(mapping_key: str):
    """删除指定的 ADK 后端"""
    manager = get_backend_manager()
    success = manager.remove_backend(mapping_key)
    return BackendResponse(
        success=success,
        message=f"Backend {mapping_key} removed successfully" if success else f"Backend {mapping_key} not found",
        mapping_key=mapping_key
    )


@router.get("/backends/{mapping_key}", summary="获取单个后端详情")
async def get_backend_detail(mapping_key: str):
    """获取指定后端的详细配置"""
    manager = get_backend_manager()
    backend = manager.get_backend(mapping_key)
    if backend:
        return {"success": True, "mapping_key": mapping_key, "backend": backend}
    raise HTTPException(status_code=404, detail=f"Backend {mapping_key} not found")


@router.post("/backends/reload", response_model=ReloadResponse, summary="重新加载后端配置")
async def reload_backends():
    """从环境变量重新加载后端配置"""
    manager = get_backend_manager()
    manager.reload_from_env()
    backends = manager.list_backends()
    return ReloadResponse(
        success=True,
        message="Backends reloaded from environment",
        count=len(backends)
    )


@router.post("/backends/export", response_model=ExportResponse, summary="导出后端配置")
async def export_backends(request: ExportRequest = Body(...)):
    """导出后端配置到 JSON 文件"""
    manager = get_backend_manager()
    success = manager.export_to_file(request.filepath)
    return ExportResponse(
        success=success,
        message=f"Backends exported to {request.filepath}" if success else "Export failed"
    )


@router.post("/backends/import", response_model=BackendImportResponse, summary="导入后端配置")
async def import_backends(request: BackendImportRequest = Body(...)):
    """从 JSON 文件导入后端配置"""
    manager = get_backend_manager()
    count = manager.load_from_file(request.filepath, request.replace)
    return BackendImportResponse(
        success=count > 0,
        message=f"Loaded {count} backends from {request.filepath}",
        imported_count=count
    )


# ==================== 后端健康检查 ====================

@router.get("/backends/health", response_model=AllBackendsHealthResponse, summary="检查所有后端健康状态")
async def check_backends_health():
    """
    检查所有已配置后端的健康状态。

    返回每个后端的连接状态、延迟和可用性信息。
    """
    manager = get_backend_manager()
    backends = manager.get_all_enabled_backends()

    if not backends:
        return AllBackendsHealthResponse(
            status="error",
            summary=BackendHealthSummary(total=0, healthy=0, unhealthy=0),
            backends={}
        )

    async def check_single_backend(mapping_key: str, url: str) -> tuple:
        """检查单个后端的健康状态"""
        result = SingleBackendHealthResult(
            url=url,
            status=BackendHealthStatus.UNKNOWN,
            latency_ms=None,
            error=None,
            models_count=None
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                start_time = time.time()
                response = await client.get(f"{url}/")
                latency = (time.time() - start_time) * 1000
                result.latency_ms = round(latency, 2)

                if response.status_code < 500:
                    result.status = BackendHealthStatus.HEALTHY

                    # 尝试获取模型列表
                    try:
                        models_response = await client.get(f"{url}/list-apps", timeout=3.0)
                        if models_response.status_code == 200:
                            models_data = models_response.json()
                            result.models_count = len(models_data) if isinstance(models_data, list) else None
                    except Exception:
                        pass
                else:
                    result.status = BackendHealthStatus.UNHEALTHY
                    result.error = f"HTTP {response.status_code}"
                    return mapping_key, result, False

        except httpx.TimeoutException:
            result.status = BackendHealthStatus.TIMEOUT
            result.error = "Connection timeout (>5s)"
            return mapping_key, result, False
        except httpx.ConnectError as e:
            result.status = BackendHealthStatus.UNREACHABLE
            result.error = f"Connection refused"
            return mapping_key, result, False
        except Exception as e:
            result.status = BackendHealthStatus.ERROR
            result.error = str(e)[:100]
            return mapping_key, result, False

        return mapping_key, result, True

    # 并发检查所有后端
    tasks = [check_single_backend(k, v) for k, v in backends.items()]
    check_results = await asyncio.gather(*tasks)

    results = {}
    all_healthy = True

    for mapping_key, result, is_healthy in check_results:
        results[mapping_key] = result
        if not is_healthy:
            all_healthy = False

    healthy_count = sum(1 for r in results.values() if r.status == BackendHealthStatus.HEALTHY)
    total_count = len(results)

    return AllBackendsHealthResponse(
        status="healthy" if all_healthy else "degraded",
        summary=BackendHealthSummary(
            total=total_count,
            healthy=healthy_count,
            unhealthy=total_count - healthy_count
        ),
        backends=results
    )


@router.get("/backends/{mapping_key}/health", response_model=SingleBackendHealthResponse, summary="检查单个后端健康状态")
async def check_single_backend_health(mapping_key: str):
    """检查指定后端的健康状态"""
    manager = get_backend_manager()
    backend = manager.get_backend(mapping_key)

    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{mapping_key}' not found")

    url = backend["url"]
    result = SingleBackendHealthResponse(
        mapping_key=mapping_key,
        url=url,
        status=BackendHealthStatus.UNKNOWN,
        latency_ms=None,
        error=None,
        models=None
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            start_time = time.time()
            response = await client.get(f"{url}/")
            latency = (time.time() - start_time) * 1000
            result.latency_ms = round(latency, 2)

            if response.status_code < 500:
                result.status = BackendHealthStatus.HEALTHY

                # 获取模型列表
                try:
                    models_response = await client.get(f"{url}/list-apps", timeout=3.0)
                    if models_response.status_code == 200:
                        models_data = models_response.json()
                        if isinstance(models_data, list):
                            result.models = [
                                {"name": m.get("name", m) if isinstance(m, dict) else m}
                                for m in models_data
                            ]
                except Exception as e:
                    pass
            else:
                result.status = BackendHealthStatus.UNHEALTHY
                result.error = f"HTTP {response.status_code}"

    except httpx.TimeoutException:
        result.status = BackendHealthStatus.TIMEOUT
        result.error = "Connection timeout (>5s)"
    except httpx.ConnectError:
        result.status = BackendHealthStatus.UNREACHABLE
        result.error = "Connection refused"
    except Exception as e:
        result.status = BackendHealthStatus.ERROR
        result.error = str(e)

    return result


# ==================== 配置管理 ====================

@router.get("/config", response_model=ConfigResponse, summary="获取当前配置")
async def get_current_config():
    """获取当前配置信息（隐藏敏感信息）"""
    from app.core.config import get_settings
    settings = get_settings()

    return ConfigResponse(
        server=ServerConfig(
            port=settings.port,
            log_level=settings.log_level
        ),
        features=FeaturesConfig(
            api_key_auth=settings.enable_api_key_auth,
            session_history=settings.session_history_enabled,
            metrics=settings.enable_metrics
        ),
        limits=LimitsConfig(
            max_file_size_mb=settings.max_file_size_mb,
            file_download_timeout=settings.file_download_timeout,
            max_concurrent_downloads=settings.max_concurrent_downloads
        ),
        database=DatabaseConfig(
            path=settings.database_path,
            retention_days=settings.session_retention_days
        ),
        backends=BackendsConfig(
            count=len(settings.adk_backend_mapping),
            keys=list(settings.adk_backend_mapping.keys())
        )
    )


@router.post("/config/reload", response_model=ConfigReloadResponse, summary="重新加载配置")
async def reload_config():
    """从环境变量重新加载配置"""
    from app.core.config import reload_settings
    from dotenv import load_dotenv

    # 重新加载 .env 文件
    load_dotenv(override=True)

    # 重新创建配置实例
    new_settings = reload_settings()

    # 验证新配置
    errors = new_settings.validate_required_config()

    return ConfigReloadResponse(
        success=True,
        message="Configuration reloaded from environment",
        validation=ConfigValidationResult(
            valid=len(errors) == 0,
            errors=errors if errors else None,
            warnings=None
        ),
        config={
            "port": new_settings.port,
            "log_level": new_settings.log_level,
            "backends_count": len(new_settings.adk_backend_mapping)
        }
    )


@router.get("/config/validate", response_model=ConfigValidationResult, summary="验证当前配置")
async def validate_config():
    """验证当前配置是否正确"""
    from app.core.config import get_settings
    settings = get_settings()

    errors = settings.validate_required_config()
    warnings = []

    # 额外的运行时检查
    if settings.adk_backend_mapping:
        from app.core.backend_manager import get_backend_manager
        backend_manager = get_backend_manager()
        enabled_backends = backend_manager.get_all_enabled_backends()

        if not enabled_backends:
            warnings.append("No enabled backends available")

    return ConfigValidationResult(
        valid=len(errors) == 0,
        errors=errors if errors else None,
        warnings=warnings if warnings else None
    )
