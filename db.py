"""
SQLite persistence untuk video /e/slug-ID
- Simpan metadata saat get_video berhasil
- Baca dulu dari DB sebelum hit API (opsional, via cache_ttl)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from config import STORE_DIR, get as cfg

_DB_PATH = STORE_DIR / "agc.db"
_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id            TEXT PRIMARY KEY,
                    slug          TEXT NOT NULL DEFAULT '',
                    title         TEXT NOT NULL DEFAULT '',
                    raw_title     TEXT NOT NULL DEFAULT '',
                    duration      TEXT NOT NULL DEFAULT '',
                    size          TEXT NOT NULL DEFAULT '',
                    views         TEXT NOT NULL DEFAULT '',
                    thumb         TEXT NOT NULL DEFAULT '',
                    embed         TEXT NOT NULL DEFAULT '',
                    download      TEXT NOT NULL DEFAULT '',
                    api_source    TEXT NOT NULL DEFAULT '',
                    channel       TEXT NOT NULL DEFAULT '',
                    uploaded      TEXT NOT NULL DEFAULT '',
                    payload_json  TEXT NOT NULL DEFAULT '{}',
                    hit_count     INTEGER NOT NULL DEFAULT 0,
                    created_at    REAL NOT NULL,
                    updated_at    REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_videos_slug ON videos(slug);
                CREATE INDEX IF NOT EXISTS idx_videos_updated ON videos(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title);

                CREATE TABLE IF NOT EXISTS downloads_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id   TEXT NOT NULL,
                    slug       TEXT NOT NULL DEFAULT '',
                    path       TEXT NOT NULL DEFAULT '',
                    ip         TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dl_video ON downloads_log(video_id);
                CREATE INDEX IF NOT EXISTS idx_dl_created ON downloads_log(created_at DESC);
                """
            )
            conn.commit()
            _initialized = True
        finally:
            conn.close()


def _slug_from_title(title: str) -> str:
    import re
    t = re.sub(r"[^\w\s-]", "", title or "", flags=re.UNICODE)
    t = re.sub(r"[\s_]+", "-", t.strip())
    t = re.sub(r"-+", "-", t).lower().strip("-")
    return t or "n-a"


def save_video(video: dict) -> bool:
    """Upsert metadata video ke SQLite. Dipanggil setelah API sukses."""
    if not cfg("enable_sqlite", True):
        return False
    if not video or not video.get("id"):
        return False

    init_db()
    vid = str(video["id"]).strip()
    title = str(video.get("title") or "")
    slug = _slug_from_title(title)
    now = time.time()

    # Simpan full payload supaya field baru tidak hilang
    payload = dict(video)
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO videos (
                    id, slug, title, raw_title, duration, size, views, thumb,
                    embed, download, api_source, channel, uploaded,
                    payload_json, hit_count, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, 1, ?, ?
                )
                ON CONFLICT(id) DO UPDATE SET
                    slug=excluded.slug,
                    title=excluded.title,
                    raw_title=excluded.raw_title,
                    duration=excluded.duration,
                    size=excluded.size,
                    views=excluded.views,
                    thumb=excluded.thumb,
                    embed=excluded.embed,
                    download=excluded.download,
                    api_source=excluded.api_source,
                    channel=excluded.channel,
                    uploaded=excluded.uploaded,
                    payload_json=excluded.payload_json,
                    hit_count=videos.hit_count + 1,
                    updated_at=excluded.updated_at
                """,
                (
                    vid,
                    slug,
                    title,
                    str(video.get("raw_title") or ""),
                    str(video.get("duration") or ""),
                    str(video.get("size") or ""),
                    str(video.get("views") or "0"),
                    str(video.get("thumb") or video.get("single_img") or ""),
                    str(video.get("embed") or video.get("protected_embed") or ""),
                    str(video.get("download") or video.get("protected_dl") or ""),
                    str(video.get("api_source") or ""),
                    str(video.get("channel") or ""),
                    str(video.get("uploaded") or ""),
                    payload_json,
                    now,
                    now,
                ),
            )
            conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            conn.close()


def get_video_db(video_id: str) -> Optional[dict]:
    """Ambil video dari SQLite. Return dict mirip API atau None."""
    if not cfg("enable_sqlite", True):
        return None
    video_id = (video_id or "").strip()
    if not video_id:
        return None

    init_db()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload_json, updated_at FROM videos WHERE id = ?",
                (video_id,),
            ).fetchone()
            if not row:
                return None

            # Optional: anggap stale jika lebih tua dari cache_video TTL
            ttl = int(cfg("cache_video", 2592000))
            if ttl > 0 and (time.time() - float(row["updated_at"])) > ttl:
                return None

            data = json.loads(row["payload_json"])
            return data if isinstance(data, dict) else None
        except (sqlite3.Error, json.JSONDecodeError, TypeError):
            return None
        finally:
            conn.close()


def log_download(
    video_id: str,
    slug: str = "",
    path: str = "",
    ip: str = "",
    user_agent: str = "",
) -> None:
    """Catat akses halaman /e/... (opsional analytics)."""
    if not cfg("enable_sqlite", True) or not cfg("enable_download_log", True):
        return
    init_db()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO downloads_log (video_id, slug, path, ip, user_agent, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (video_id, slug, path, ip, user_agent[:500], time.time()),
            )
            conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()


def list_recent_videos(limit: int = 50) -> list[dict]:
    """Daftar video terbaru di DB (untuk admin / sitemap)."""
    if not cfg("enable_sqlite", True):
        return []
    init_db()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT id, slug, title, duration, size, views, thumb, updated_at, hit_count
                FROM videos
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()


def video_count() -> int:
    if not cfg("enable_sqlite", True):
        return 0
    init_db()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()
            return int(row["c"]) if row else 0
        except sqlite3.Error:
            return 0
        finally:
            conn.close()
