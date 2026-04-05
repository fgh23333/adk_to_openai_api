# -*- coding: utf-8 -*-
from typing import Dict, Optional, List
from datetime import datetime
import threading
import logging
import json
import os
from app.core.config import settings

logger = logging.getLogger(__name__)
BACKENDS_PERSIST_FILE = 'data/backends.json'

class BackendManager:
    def __init__(self, persist_file=None):
        self._backends = {}
        self._lock = threading.RLock()
        self._persist_file = persist_file or BACKENDS_PERSIST_FILE
        self._load_backends()

    def _load_backends(self):
        if self._load_from_persist_file():
            logger.info(f'Loaded {len(self._backends)} backends from persist file')
            return
        self._load_from_env()
        logger.info(f'Loaded {len(self._backends)} backends from environment')

    def _load_from_env(self):
        for k, v in settings.adk_backend_mapping.items():
            self._backends[k] = {'url': v, 'added_at': datetime.now().isoformat(), 'source': 'env', 'enabled': True, 'description': ''}

    def _load_from_persist_file(self):
        try:
            if not os.path.exists(self._persist_file): return False
            with open(self._persist_file, 'r', encoding='utf-8') as f: data = json.load(f)
            backends = data.get('backends', {})
            if not isinstance(backends, dict): return False
            for k, v in backends.items():
                self._backends[k] = {'url': v.get('url', ''), 'added_at': v.get('added_at', datetime.now().isoformat()), 'source': v.get('source', 'file'), 'enabled': v.get('enabled', True), 'description': v.get('description', '')}
            return True
        except Exception as e:
            logger.warning(f'Failed to load persist file: {e}')
            return False

    def _save_to_persist_file(self):
        try:
            d = os.path.dirname(self._persist_file)
            if d: os.makedirs(d, exist_ok=True)
            with self._lock:
                data = {'saved_at': datetime.now().isoformat(), 'backends': dict(self._backends)}
            with open(self._persist_file, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f'Failed to save: {e}')
            return False

    def add_backend(self, mapping_key, url, description=''):
        with self._lock:
            if mapping_key in self._backends: return False
            self._backends[mapping_key] = {'url': url, 'added_at': datetime.now().isoformat(), 'source': 'dynamic', 'enabled': True, 'description': description}
        self._save_to_persist_file()
        return True

    def remove_backend(self, mapping_key):
        with self._lock:
            if mapping_key not in self._backends: return False
            del self._backends[mapping_key]
        self._save_to_persist_file()
        return True

    def update_backend(self, mapping_key, url=None, description=None, enabled=None):
        with self._lock:
            if mapping_key not in self._backends: return False
            b = self._backends[mapping_key]
            if url is not None: b['url'] = url
            if description is not None: b['description'] = description
            if enabled is not None: b['enabled'] = enabled
            b['updated_at'] = datetime.now().isoformat()
        self._save_to_persist_file()
        return True

    def get_backend(self, mapping_key): return self._backends.get(mapping_key)
    def get_backend_url(self, mapping_key):
        b = self._backends.get(mapping_key)
        return b['url'] if b and b.get('enabled', True) else None
    def list_backends(self, include_disabled=False):
        r = []
        for k, v in self._backends.items():
            if include_disabled or v.get('enabled', True):
                r.append({'mapping_key': k, 'url': v['url'], 'enabled': v.get('enabled', True), 'added_at': v['added_at'], 'source': v['source'], 'description': v.get('description', ''), 'updated_at': v.get('updated_at')})
        return r
    def get_all_enabled_backends(self): return {k: v['url'] for k, v in self._backends.items() if v.get('enabled', True)}
    def get_all_enabled_keys(self): return [k for k, v in self._backends.items() if v.get('enabled', True)]
    def has_backend(self, mapping_key): b = self._backends.get(mapping_key); return b is not None and b.get('enabled', True)
    def reload_from_env(self):
        with self._lock: self._backends.clear(); self._load_from_env()
        self._save_to_persist_file()
    def export_to_file(self, filepath):
        try:
            with self._lock: data = {'exported_at': datetime.now().isoformat(), 'backends': dict(self._backends)}
            d = os.path.dirname(filepath)
            if d: os.makedirs(d, exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e: logger.error(f'Export failed: {e}'); return False
    def load_from_file(self, filepath, replace=False):
        try:
            with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
            backends = data.get('backends', {})
            if not isinstance(backends, dict): return 0
            c = 0
            with self._lock:
                if replace: self._backends.clear()
                for k, v in backends.items():
                    if k and k not in self._backends:
                        self._backends[k] = {'url': v.get('url', ''), 'added_at': v.get('added_at', datetime.now().isoformat()), 'source': v.get('source', 'file'), 'enabled': v.get('enabled', True), 'description': v.get('description', '')}
                        c += 1
            if c > 0: self._save_to_persist_file()
            return c
        except Exception as e: logger.error(f'Load failed: {e}'); return 0
    def to_mapping_string(self): return ','.join([f'{k}:{v["url"]}' for k, v in self._backends.items() if v.get('enabled', True)])

_backend_manager = None
def get_backend_manager():
    global _backend_manager
    if _backend_manager is None: _backend_manager = BackendManager()
    return _backend_manager
