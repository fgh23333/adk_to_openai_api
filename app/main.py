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
from app.core.config import settings, get_request_id, set_request_id
from app.core.auth import auth, verify_api_key_dependency
from app.core.metrics import get_metrics_collector
from app.core.api_key_manager import get_api_key_manager
from app.database.database import init_database

# 路由
from app.routers import chat, admin

logger = logging.getLogger(__name__)

# Context variable for request ID
request_id_var: ContextVar[str] = ContextVar('request_id', default='')


# ========================================