from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Union
from app.config import settings
import logging
import hashlib

logger = logging.getLogger(__name__)

# Initialize HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


class APIKeyAuth:
    def __init__(self):
        self.require_api_key = settings.require_api_key
        self.api_keys = settings.api_keys if settings.api_keys else [settings.default_api_key]

    async def verify_api_key(self, credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> Union[bool, str]:
        """
        Verify API Key from Authorization header.

        Args:
            credentials: HTTP Authorization credentials

        Returns:
            str: The API key (can be used as session identifier)

        Raises:
            HTTPException: If API key is invalid or missing
        """
        # If API key verification is disabled, return default key
        if not self.require_api_key:
            return settings.default_api_key

        # If no credentials provided
        if not credentials:
            logger.warning("Missing API key in Authorization header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "message": "You didn't provide an API key. You need to provide your API key in an Authorization header using Bearer auth (i.e. Authorization: Bearer YOUR_API_KEY).",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "missing_api_key"
                    }
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract API key from Bearer token
        api_key = credentials.credentials

        # Validate API key
        if api_key not in self.api_keys:
            logger.warning(f"Invalid API key provided: {api_key[:10]}...")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "message": "Invalid API key provided.",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "invalid_api_key"
                    }
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        logger.debug(f"API key validated successfully: {api_key[:10]}...")
        return api_key

    @staticmethod
    def get_session_id_from_api_key(api_key: str) -> str:
        """Generate a stable session ID from API key."""
        # Use hash to create a stable session ID
        key_hash = hashlib.md5(api_key.encode()).hexdigest()[:12]
        return f"session_{key_hash}"


# Create global auth instance
auth = APIKeyAuth()


async def verify_api_key_dependency(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> str:
    """
    FastAPI dependency for API key verification.
    Returns the API key string (can be used as session identifier).
    """
    return await auth.verify_api_key(credentials)