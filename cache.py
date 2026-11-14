"""File cache sederhana — thread-safe, atomic write."""

from __future__ import annotations

import json
import time
import hashlib
import tempfile
import os
from pathlib import Path
from typing import Any, Optional

from config import CACHE_DIR, get as cfg


def _path(key: str, sub: str = "general") -> Path:
    h = hashlib.md5(key.encode()).hexdigest()
    d = CACHE_DIR / sub / h[:2]
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.json"


def cache_get(key: str, ttl: int = 3600, sub: str = "general") -> Optional[Any]:
    if not cfg("enable_cache", True):
        return None
    f = _path(key, sub)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "expires" not in data or "value" not in data:
            return None
        if time.time() > data["expires"]:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return data["value"]
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def cache_set(key: str, value: Any, ttl: int = 3600, sub: str = "general") -> bool:
    if not cfg("enable_cache", True):
        return False
    f = _path(key, sub)
    payload = {"expires": time.time() + ttl, "value": value}
    try:
        # Atomic write: temp file di folder yang sama lalu rename
        fd, tmp = tempfile.mkstemp(dir=str(f.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, f)
            return True
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        return False


def cache_clear_sub(sub: str) -> int:
    """Hapus semua file di subfolder cache. Return jumlah file terhapus."""
    root = CACHE_DIR / sub
    if not root.exists():
        return 0
    n = 0
    for p in root.rglob("*.json"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def cache_clean_old(max_age: int = 2592000) -> int:
    """Hapus cache lebih tua dari max_age detik."""
    if not CACHE_DIR.exists():
        return 0
    now = time.time()
    n = 0
    for p in CACHE_DIR.rglob("*.json"):
        try:
            if now - p.stat().st_mtime > max_age:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n
