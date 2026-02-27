"""
动态 API Key 管理模块

支持运行时添加、删除、列出 API key，无需重启服务
"""
from typing import List, Dict, Optional
from datetime import datetime
import threading
from app.core.config import settings
import logging
import json

logger = logging.getLogger(__name__)


class APIKeyManager:
    """动态 API Key 管理器"""

    def __init__(self):
        # 从环境变量加载初始 keys
        self._keys: Dict[str, dict] = {}
        self._lock = threading.RLock()
        self._load_initial_keys()

    def _load_initial_keys(self):
        """从环境变量加载初始 API keys"""
        initial_keys = settings.api_keys if settings.api_keys else [settings.default_api_key]
        for key in initial_keys:
            self._keys[key] = {
                "key": key,
                "added_at": datetime.now().isoformat(),
                "source": "env"
            }
        logger.info(f"Loaded {len(self._keys)} initial API keys from environment")

    def add_key(self, api_key: str, metadata: dict = None) -> bool:
        """
        添加 API key

        Args:
            api_key: API key 字符串
            metadata: 可选的元数据（如用户名、描述等）

        Returns:
            是否添加成功（如果 key 已存在则返回 False）
        """
        with self._lock:
            if api_key in self._keys:
                logger.warning(f"API key already exists: {api_key[:10]}...")
                return False

            self._keys[api_key] = {
                "key": api_key,
                "added_at": datetime.now().isoformat(),
                "source": "dynamic",
                "metadata": metadata or {}
            }
            logger.info(f"Added new API key: {api_key[:10]}...")
            return True

    def remove_key(self, api_key: str) -> bool:
        """
        删除 API key

        Args:
            api_key: 要删除的 API key

        Returns:
            是否删除成功
        """
        with self._lock:
            if api_key not in self._keys:
                logger.warning(f"API key not found: {api_key[:10]}...")
                return False

            del self._keys[api_key]
            logger.info(f"Removed API key: {api_key[:10]}...")
            return True

    def has_key(self, api_key: str) -> bool:
        """检查 API key 是否存在"""
        with self._lock:
            return api_key in self._keys

    def list_keys(self, include_value: bool = False) -> List[dict]:
        """
        列出所有 API keys

        Args:
            include_value: 是否返回完整的 key 值（默认只返回前缀）

        Returns:
            API key 列表
        """
        with self._lock:
            result = []
            for key_info in self._keys.values():
                if include_value:
                    result.append(key_info.copy())
                else:
                    result.append({
                        "prefix": key_info["key"][:10] + "...",
                        "added_at": key_info["added_at"],
                        "source": key_info["source"],
                        "metadata": key_info.get("metadata", {})
                    })
            return result

    def get_all_keys(self) -> List[str]:
        """获取所有有效的 API key 值（用于验证）"""
        with self._lock:
            return list(self._keys.keys())

    def reload_from_env(self):
        """从环境变量重新加载 API keys"""
        with self._lock:
            self._keys.clear()
            self._load_initial_keys()
            logger.info("Reloaded API keys from environment")

    def export_to_file(self, filepath: str) -> bool:
        """
        导出当前 API keys 到 JSON 文件（备份）

        Args:
            filepath: 导出文件路径

        Returns:
            是否成功
        """
        try:
            with self._lock:
                data = {
                    "exported_at": datetime.now().isoformat(),
                    "keys": list(self._keys.keys())
                }
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                logger.info(f"Exported {len(self._keys)} API keys to {filepath}")
                return True
        except Exception as e:
            logger.error(f"Failed to export API keys: {e}")
            return False

    def load_from_file(self, filepath: str, replace: bool = False) -> int:
        """
        从文件导入 API keys

        Args:
            filepath: JSON 文件路径
            replace: 是否替换现有 keys（默认追加）

        Returns:
            导入的 key 数量
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            keys = data.get("keys", [])
            if not isinstance(keys, list):
                logger.error(f"Invalid keys file format: expected list")
                return 0

            with self._lock:
                if replace:
                    self._keys.clear()

                count = 0
                for key in keys:
                    if key and key not in self._keys:
                        self._keys[key] = {
                            "key": key,
                            "added_at": datetime.now().isoformat(),
                            "source": "file"
                        }
                        count += 1
                logger.info(f"Loaded {count} API keys from {filepath}")
                return count
        except Exception as e:
            logger.error(f"Failed to load API keys from file: {e}")
            return 0


# 全局实例
_api_key_manager: Optional[APIKeyManager] = None


def get_api_key_manager() -> APIKeyManager:
    """获取全局 API Key 管理器实例"""
    global _api_key_manager
    if _api_key_manager is None:
        _api_key_manager = APIKeyManager()
    return _api_key_manager
