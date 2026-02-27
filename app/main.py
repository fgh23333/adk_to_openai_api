"""
ADK Middleware API - 主应用入口

将 Google ADK Agent 转换为 OpenAI 兼容的 Chat Completions API
"""
import logging
import base64
import uuid
import time
import hashlib
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Dict, Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

# 核心配置和工具
from app.core.config import settings
from app.core.auth import auth, verify_api_key_dependency, get_request_id, set_request_id
from app.core.metrics import get_metrics_collector
from app.api_key_manager import get_api_key_manager
from app.database.database import init_database

# 路由
from app.routers import chat, admin

logger = logging.getLogger(__name__)

# Context variable for request ID
request_id_var: ContextVar[str] = ContextVar('request_id', default='')


# ========================================
# 请求追踪中间件
# ========================================
class RequestTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get('X-Request-ID') or f"req_{uuid.uuid4().hex[:12]}"
        set_request_id(request_id)

        start_time = time.time()
        response = await call_next(request)

        process_time = time.time() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.3f}s"

        logger.info(f"Request completed in {process_time:.3f}s")
        return response


# ========================================
# 应用生命周期
# ========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    # Startup
    logger.info("ADK Middleware starting up...")

    # Initialize database
    if settings.session_history_enabled:
        from app.database.database import get_database
        db = get_database()
        logger.info(f"Session history database initialized: {settings.database_path}")

    logger.info("Metrics collector initialized")
    yield

    # Shutdown
    logger.info("ADK Middleware shutting down...")

    from app.core.metrics import get_metrics_collector
    metrics_collector = get_metrics_collector()
    await metrics_collector.cleanup_old_requests()

    from app.core.adk_client import ADKClient
    adk_client_instance = ADKClient()
    await adk_client_instance.close()
    logger.info("Shutdown complete")


# ========================================
# 创建 FastAPI 应用
# ========================================
app = FastAPI(
    title="ADK Middleware API",
    description="""
## 概述

将 Google ADK Agent 转换为 OpenAI 兼容的 Chat Completions API。

## 功能特性

- ✅ **多模态支持**: 图片、视频、音频、PDF、Office文档
- ✅ **流式响应**: 真正的 SSE 流式输出
- ✅ **多会话管理**: 通过 API Key 区分用户会话
- ✅ **动态 API Key**: 运行时添加/删除 API Key 无需重启

## 认证

使用 Bearer Token 认证，格式: `Bearer sk-your-api-key`
""",
    version="1.3.0",
    lifespan=lifespan,
    contact={
        "name": "ADK Middleware",
    },
)

# ========================================
# 中间件配置
# ========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestTrackingMiddleware)


# ========================================
# 异常处理
# ========================================
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with detailed error messages."""
    error_types = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        500: "internal_error",
        502: "bad_gateway",
        503: "service_unavailable",
        504: "gateway_timeout",
    }
    error_type = error_types.get(exc.status_code, "unknown_error")

    logger.warning(f"HTTP {exc.status_code}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": str(exc.detail),
                "type": error_type,
                "code": exc.status_code
            }
        }
    )


@app.exception_handler(httpx.TimeoutException)
async def timeout_error_handler(request: Request, exc: httpx.TimeoutException):
    """Handle timeout errors."""
    logger.error(f"Request timeout: {exc}")
    return JSONResponse(
        status_code=504,
        content={
            "error": {
                "message": "Request timed out while communicating with ADK backend",
                "type": "timeout_error"
            }
        }
    )


@app.exception_handler(httpx.HTTPStatusError)
async def http_status_error_handler(request: Request, exc: httpx.HTTPStatusError):
    """Handle ADK HTTP errors with detailed messages."""
    status = exc.response.status_code

    try:
        error_body = exc.response.json()
        adk_message = error_body.get('error', {}).get('message', str(error_body))
    except:
        adk_message = exc.response.reason_phrase

    logger.error(f"ADK HTTP error {status}: {adk_message}")

    if status >= 500:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"ADK backend error: {adk_message}",
                    "type": "adk_backend_error"
                }
            }
        )
    elif status == 400:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": f"Request rejected by ADK: {adk_message}",
                    "type": "adk_bad_request"
                }
            }
        )
    elif status == 404:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": "ADK resource not found. Session may have expired.",
                    "type": "adk_not_found"
                }
            }
        )
    else:
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "message": f"ADK request failed: {adk_message}",
                    "type": "adk_error"
                }
            }
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unexpected errors."""
    request_id = get_request_id()
    logger.error(f"Unhandled exception [request_id={request_id}]: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "An unexpected error occurred",
                "type": "internal_error"
            }
        }
    )


# ========================================
# 注册路由
# ========================================
app.include_router(chat.router)
app.include_router(admin.router)


# ========================================
# 健康检查和指标端点（放在最后作为默认路由）
# ========================================
@app.get("/v1/health/detailed")
async def health_check_detailed() -> dict:
    """Detailed health check including ADK backend status."""
    from app.core.adk_client import ADKClient
    adk_client = ADKClient()
    health_status = await adk_client.check_health()
    return health_status


@app.get("/v1/metrics")
async def get_metrics() -> str:
    """Get Prometheus-compatible metrics."""
    from app.core.metrics import get_metrics_collector
    metrics_collector = get_metrics_collector()
    return metrics_collector.get_prometheus_metrics()


# ========================================
# 主程序入口
# ========================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower()
    )
