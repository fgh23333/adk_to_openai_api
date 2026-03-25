from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import logging
import hashlib

logger = logging.getLogger(__name__)

# Initialize HTTP Bearer security scheme (no auto error)
security = HTTPBearer(auto_error=False)


class APIKeyAuth:
    """
    简化的认证：直接使用上游 token 做租户区分，不做验证。

    租户策略：
    - 如果有 Authorization header → 使用其 hash 作为租户 ID
    - 如果没有 Authorization header → 使用默认租户 ID
    """

    async def verify_api_key(self, credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> str:
        """
        从 Authorization header 获取 token，直接用作租户标识。

        不做任何验证，只用于租户区分。

        Args:
            credentials: HTTP Authorization credentials

        Returns:
            str: 租户 ID（基于 token 的 hash）
        """
        if not credentials:
            # 没有提供 token，使用默认租户
            logger.debug("No Authorization header, using default tenant")
            return "tenant_default"

        # 提取 token
        token = credentials.credentials

        # 使用 token 的 hash 作为租户 ID（避免泄露原始 token）
        tenant_id = self.get_tenant_id_from_token(token)

        logger.debug(f"Request from tenant: {tenant_id}")
        return tenant_id

    @staticmethod
    def get_tenant_id_from_token(token: Optional[str]) -> str:
        """
        从 token 生成租户标识符

        使用 token 的 MD5 hash 作为租户 ID，确保：
        1. 不同 token 的租户完全隔离
        2. 日志中不会泄露原始 token
        3. 同一 token 始终对应同一租户
        """
        if not token:
            return "tenant_default"

        # 使用 token 的 MD5 hash 作为租户 ID
        token_hash = hashlib.md5(token.encode()).hexdigest()[:16]
        return f"tenant_{token_hash}"

    @staticmethod
    def get_session_id_from_api_key(api_key: Optional[str]) -> str:
        """
        向后兼容：从 API key 生成用户标识符

        现在直接使用 token 的 hash，与 get_tenant_id_from_token 相同
        """
        if not api_key:
            return "tenant_default"

        key_hash = hashlib.md5(api_key.encode()).hexdigest()[:16]
        return f"tenant_{key_hash}"


# Create global auth instance
auth = APIKeyAuth()


async def verify_api_key_dependency(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> str:
    """
    FastAPI dependency for API key verification.

    不做验证，直接返回租户 ID。
    """
    return await auth.verify_api_key(credentials)
