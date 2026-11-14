"""
AGC Core — Python edition
Lebih ringan (stdlib), concurrent HTTP, cache atomic, API setara AGC.php
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import CONFIG, STORE_DIR, get as cfg
from cache import cache_get, cache_set, cache_clear_sub, cache_clean_old
from db import get_video_db, save_video


# ---------------------------------------------------------------------------
# HTTP (stdlib, parallel via ThreadPoolExecutor)
# ---------------------------------------------------------------------------

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def _ua() -> str:
    return random.choice(_UA_POOL)


def http_get(url: str, timeout: Optional[float] = None) -> Optional[dict]:
    timeout = timeout or cfg("http_timeout", 12)
    req = Request(
        url,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "User-Agent": _ua(),
            "Referer": "https://doodapi.co/",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            return data if isinstance(data, dict) else None
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def http_get_multi(urls: list[str], timeout: Optional[float] = None) -> list[Optional[dict]]:
    """Parallel GET — mirip Guzzle Pool / curl_multi."""
    if not urls:
        return []
    workers = min(cfg("max_workers", 6), len(urls))
    results: list[Optional[dict]] = [None] * len(urls)

    def _fetch(i: int, u: str):
        return i, http_get(u, timeout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_fetch, i, u) for i, u in enumerate(urls)]
        for fut in as_completed(futs):
            try:
                i, data = fut.result()
                results[i] = data
            except Exception:
                pass
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_bytes(n: int, precision: int = 1) -> str:
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    if n < 1048576:
        return f"{round(n / 1024, precision)} KB"
    if n < 1073741824:
        return f"{round(n / 1048576, precision)} MB"
    return f"{round(n / 1073741824, 2)} GB"


def format_duration(seconds: Any) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "00:00"
    if s <= 0:
        return "00:00"
    if s >= 3600:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"
    m, sec = divmod(s, 60)
    return f"{m:02d}:{sec:02d}"


def similar_ratio(a: str, b: str) -> float:
    """Mirip similar_text() PHP — rasio 0..100."""
    if not a or not b:
        return 0.0
    if a == b:
        return 100.0
    # difflib ratio * 100
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() * 100


def levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 10:
        return 999
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            ins, delete, sub = prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)
            curr.append(min(ins, delete, sub))
        prev = curr
    return prev[-1]


def set_recent_search(query: str) -> None:
    if not cfg("save_recent_searches", True):
        return
    query = query.strip()
    if not query:
        return
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    f = STORE_DIR / "searches.json"
    items: list = []
    if f.exists():
        try:
            items = json.loads(f.read_text(encoding="utf-8")) or []
        except (json.JSONDecodeError, OSError):
            items = []
    qlow = query.lower()
    items = [
        x for x in items
        if (x.get("title") if isinstance(x, dict) else str(x)).lower() != qlow
    ]
    items.insert(0, {"title": query, "time": int(time.time())})
    limit = int(cfg("recent_searches_limit", 5000))
    items = items[:limit]
    try:
        f.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# AGC Class
# ---------------------------------------------------------------------------

class AGC:
    def __init__(self):
        self.dood_key = str(cfg("dood_api_key", ""))
        self.lulu_key = str(cfg("lulu_api_key", ""))
        self._last_search_totals: dict[str, int] = {}

    # -------------------- SEARCH --------------------

    def search(self, query: str = "", page: int = 1) -> dict:
        query = (query or "").strip()
        if not query:
            return self._empty_result(page)

        page = max(1, int(page))
        per_page = int(cfg("search_per_page", 50))
        normalized = query.lower()
        cache_key = "search_v2_" + hashlib.md5(normalized.encode()).hexdigest()

        all_items = cache_get(cache_key, cfg("cache_search"), "search")
        if all_items is None:
            terms = [t for t in normalized.split() if t]
            if not terms:
                return self._empty_result(page)

            variations = self._build_query_variations(terms)
            urls = [
                f"https://doodapi.co/api/search/videos?key={self.dood_key}&search_term={urlencode({'': t})[1:]}"
                for t in variations
            ]
            # fix urlencode for bare term
            urls = [
                f"https://doodapi.co/api/search/videos?key={self.dood_key}&{urlencode({'search_term': t})}"
                for t in variations
            ]

            responses = http_get_multi(urls)
            raw_pool: dict[str, dict] = {}

            for data in responses:
                if not data or not isinstance(data.get("result"), list):
                    continue
                for item in data["result"]:
                    fid = str(item.get("file_code") or "").strip()
                    if not fid or fid in raw_pool:
                        continue
                    raw_title = str(item.get("title") or item.get("file_title") or "").strip()
                    title = self.get_or_cache_title(fid, raw_title)
                    relevance = self._calculate_relevance(title, terms)
                    length_raw = item.get("length") or item.get("file_length") or 0
                    uploaded = item.get("uploaded") or ""
                    try:
                        ts = int(datetime.fromisoformat(uploaded.replace("Z", "+00:00")).timestamp()) if uploaded else 0
                    except Exception:
                        ts = 0

                    raw_pool[fid] = {
                        "id": fid,
                        "file_code": fid,
                        "title": title,
                        "raw_title": raw_title,
                        "duration": format_duration(length_raw),
                        "length": format_duration(length_raw),
                        "size": format_bytes(int(item["size"])) if item.get("size") else "",
                        "views": str(item.get("views") or "0"),
                        "thumb": item.get("single_img") or item.get("splash_img") or "",
                        "single_img": item.get("single_img") or "",
                        "uploaded": uploaded,
                        "uploaded_timestamp": ts,
                        "relevance": relevance,
                        "channel": "DoodStream",
                        "api_source": "doodapi",
                    }

            all_items = self._remove_duplicate_titles(list(raw_pool.values()))
            all_items.sort(
                key=lambda x: (-x.get("relevance", 0), -x.get("uploaded_timestamp", 0))
            )
            cache_set(cache_key, all_items, cfg("cache_search"), "search")

        self._last_search_totals[normalized] = len(all_items)
        set_recent_search(query)

        total = len(all_items)
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        page_items = all_items[offset : offset + per_page]

        return {
            "items": page_items,
            "total": total,
            "total_pages": total_pages,
            "page": page,
            "query": query,
        }

    def get_search(self, q: str = "", page: int = 1) -> list:
        return self.search(q, page).get("items") or []

    def get_search_total(self, custom: Optional[str] = None) -> int:
        q = (custom if custom is not None else "").strip().lower()
        if not q:
            return 0
        if q in self._last_search_totals:
            return self._last_search_totals[q]
        self.search(q, 1)
        return self._last_search_totals.get(q, 0)

    # -------------------- VIDEO --------------------

    def get_video(self, id: str = "") -> Optional[dict]:
        id = (id or "").strip()
        if not id:
            return None

        # 1) File cache
        cache_key = "video_" + hashlib.md5(id.encode()).hexdigest()
        cached = cache_get(cache_key, cfg("cache_video"), "video")
        if cached is not None:
            return cached

        # 2) SQLite (jika masih fresh menurut TTL)
        if cfg("sqlite_prefer_db", True):
            db_hit = get_video_db(id)
            if db_hit:
                cache_set(cache_key, db_hit, cfg("cache_video"), "video")
                return db_hit

        # 3) API Dood → Lulu
        video = self._fetch_dood(id) or self._fetch_lulu(id)
        if video:
            cache_set(cache_key, video, cfg("cache_video"), "video")
            save_video(video)  # persist ke SQLite
        return video

    def get_download(self, id: Optional[str] = None) -> Optional[dict]:
        return self.get_video(str(id or ""))

    def _fetch_dood(self, fid: str) -> Optional[dict]:
        url = f"https://doodapi.co/api/file/info?key={self.dood_key}&file_code={fid}"
        data = http_get(url)
        if not data or not data.get("result"):
            return None
        item = data["result"]
        if isinstance(item, list):
            item = item[0] if item else {}
        raw_title = str(item.get("title") or item.get("file_title") or "").strip()
        title = self.get_or_cache_title(fid, raw_title)
        if not title:
            return None
        length = int(item.get("length") or item.get("file_length") or 0)
        return {
            "id": fid,
            "file_code": fid,
            "title": title,
            "raw_title": raw_title,
            "duration": format_duration(length),
            "length": format_duration(length),
            "size": format_bytes(int(item["size"])) if item.get("size") else "",
            "views": str(item.get("views") or "0"),
            "thumb": item.get("single_img") or item.get("splash_img") or "",
            "single_img": item.get("single_img") or "",
            "embed": f"https://dodl.pages.dev/{fid}",
            "protected_embed": f"https://dodl.pages.dev/{fid}",
            "download": f"https://doodstream.com/d/{fid}",
            "protected_dl": f"https://doodstream.com/d/{fid}",
            "api_source": "doodapi",
            "channel": "DoodStream",
            "uploaded": item.get("uploaded") or "",
        }

    def _fetch_lulu(self, fid: str) -> Optional[dict]:
        url = f"https://api.lulustream.com/api/file/info?key={self.lulu_key}&file_code={fid}"
        data = http_get(url)
        if not data or not data.get("result"):
            return None
        item = data["result"]
        if isinstance(item, list):
            item = item[0] if item else {}
        status = item.get("status", "")
        try:
            if int(status) in (400, 404):
                return None
        except (TypeError, ValueError):
            pass
        raw_title = str(item.get("file_title") or "").strip()
        title = self.get_or_cache_title(fid, raw_title)
        if not title:
            return None
        length = int(item.get("file_length") or 0)
        return {
            "id": fid,
            "file_code": fid,
            "title": title,
            "raw_title": raw_title,
            "duration": format_duration(length),
            "length": format_duration(length),
            "size": format_bytes(int(item["file_size"])) if item.get("file_size") else "",
            "views": str(item.get("file_views") or "0"),
            "thumb": item.get("player_img") or "",
            "single_img": item.get("player_img") or "",
            "embed": f"https://luvluv.pages.dev/{fid}",
            "protected_embed": f"https://luvluv.pages.dev/{fid}",
            "download": f"https://lulustream.com/d/{fid}",
            "protected_dl": f"https://lulustream.com/d/{fid}",
            "api_source": "lulustream",
            "channel": "LuluStream",
            "uploaded": item.get("file_created") or "",
        }

    # -------------------- RELATED --------------------

    def related(self, title: str, exclude_id: str = "", limit: int = 8) -> list:
        keywords = self._extract_keywords(title)
        if not keywords:
            return []
        result = self.search(" ".join(keywords), 1)
        items = [v for v in (result.get("items") or []) if (v.get("id") or "") != exclude_id]
        return items[:limit]

    def get_related(self, keyword: str = "", exclude_id: str = "") -> list:
        return self.related(keyword, exclude_id)

    def get_related_videos(self, title: str, raw_api: bool = False) -> list:
        return self.related(str(title))

    # -------------------- LISTS --------------------

    def latest_videos(self, page: int = 1, per_page: int = 20) -> list:
        cache_key = f"list_dood_{page}_{per_page}"
        cached = cache_get(cache_key, cfg("cache_list"), "list")
        if cached is not None:
            return cached
        url = f"https://doodapi.co/api/file/list?key={self.dood_key}&page={page}&per_page={per_page}"
        data = http_get(url)
        items = []
        files = []
        if data and isinstance(data.get("result"), dict):
            files = data["result"].get("files") or []
        elif data and isinstance(data.get("result"), list):
            files = data["result"]
        for f in files:
            fid = str(f.get("file_code") or "").strip()
            if not fid:
                continue
            raw = f.get("title") or f.get("file_title") or "Video"
            items.append({
                "id": fid,
                "title": self.get_or_cache_title(fid, raw),
                "duration": format_duration(f.get("length") or 0),
                "size": format_bytes(int(f["size"])) if f.get("size") else "",
                "views": str(f.get("views") or "0"),
                "thumb": f.get("single_img") or "",
                "api_source": "doodapi",
            })
        cache_set(cache_key, items, cfg("cache_list"), "list")
        return items

    def get_list_files(self, halaman=None, per_page=20) -> list:
        return self.latest_videos(int(halaman or 1), int(per_page))

    def get_list_lulu(self, halaman=None) -> list:
        page = max(1, int(halaman or 1))
        cache_key = f"list_lulu_{page}"
        cached = cache_get(cache_key, cfg("cache_list"), "list")
        if cached is not None:
            return cached
        url = f"https://api.lulustream.com/api/file/list?key={self.lulu_key}&per_page=30&page={page}"
        data = http_get(url)
        items = []
        files = []
        if data and isinstance(data.get("result"), dict):
            files = data["result"].get("files") or []
        elif data and isinstance(data.get("result"), list):
            files = data["result"]
        for f in files:
            fid = str(f.get("file_code") or "").strip()
            if not fid:
                continue
            raw = f.get("file_title") or f.get("title") or "Video"
            items.append({
                "id": fid,
                "title": self.get_or_cache_title(fid, raw),
                "duration": format_duration(f.get("file_length") or 0),
                "size": format_bytes(int(f["file_size"])) if f.get("file_size") else "",
                "views": str(f.get("file_views") or "0"),
                "thumb": f.get("player_img") or "",
                "api_source": "lulustream",
            })
        cache_set(cache_key, items, cfg("cache_list"), "list")
        return items

    def get_youtube_top_videos(self) -> list:
        return self.get_list_files(1)

    def get_youtube_playlist(self) -> list:
        return self.get_random_page()

    def get_total_pages(self) -> int:
        return 200

    def get_random_page(self) -> list:
        return self.get_list_files(random.randint(1, 50))

    # -------------------- TITLE --------------------

    def get_or_cache_title(self, file_code: str, raw_title: str) -> str:
        cache_key = "title_" + hashlib.md5(file_code.encode()).hexdigest()
        cached = cache_get(cache_key, cfg("cache_title"), "title")
        if cached:
            return cached
        cleaned = self.clean_title(raw_title)
        if cleaned:
            cache_set(cache_key, cleaned, cfg("cache_title"), "title")
        return cleaned

    def clean_title(self, title: Optional[str]) -> str:
        if not title:
            return ""
        t = title
        t = re.sub(r"\bbocah\s+sd\b", "Remaja", t, flags=re.I)
        t = re.sub(r"\bbocil\b", "Ucil", t, flags=re.I)
        t = re.sub(r"\bbocah\b", "Remaja", t, flags=re.I)
        t = re.sub(r"\bsd\b", "Esdeh", t, flags=re.I)
        t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
        t = re.sub(r"[^a-zA-Z\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        t = t.title()

        words = t.split()
        if len(words) < 5:
            extra = self._random_words(5 - len(words))
            words.extend(extra)

        seen = set()
        unique = []
        for w in words:
            wl = w.lower()
            if w and wl not in seen:
                unique.append(w)
                seen.add(wl)
        if len(unique) > 12:
            unique = unique[:12]
        return " ".join(unique)

    # -------------------- CATEGORY --------------------

    def resolve_category_title(self, category_name: Optional[str]) -> str:
        return (category_name or "").strip().title()

    def get_category_videos(self, category_name: Optional[str] = None) -> list:
        return self.get_search(str(category_name or ""), 1)

    def get_category_total(self, category_name: Optional[str] = None) -> int:
        return self.get_search_total(str(category_name or ""))

    # -------------------- CACHE MGMT --------------------

    def clear_search_cache(self) -> int:
        return cache_clear_sub("search")

    def clean_old_cache(self) -> int:
        return cache_clean_old(2592000)

    # -------------------- INTERNAL --------------------

    def _calculate_relevance(self, title: str, keywords: list[str]) -> float:
        if not title or not keywords:
            return 0.0
        title_lower = title.lower().strip()
        full_query = " ".join(keywords)
        score = 0.0

        if full_query in title_lower:
            score += 120
            if title_lower.startswith(full_query):
                score += 60

        matched = 0
        title_words = title_lower.split()
        for kw in keywords:
            if not kw:
                continue
            best = 0.0
            for tw in title_words:
                if not tw:
                    continue
                if tw == kw:
                    best = 1.0
                    continue
                pct = similar_ratio(tw, kw)
                lev = levenshtein(tw, kw) if len(tw) <= 255 and len(kw) <= 255 else 999
                if pct >= 85 or lev <= 1:
                    best = max(best, 0.85)
                elif pct >= 70 or lev <= 3:
                    best = max(best, 0.55)
            if best > 0:
                score += best * 9
                matched += 1

        if matched == len(keywords) and len(keywords) > 1:
            score += 40
        elif matched >= len(keywords) - 1 and len(keywords) > 2:
            score += 22

        for kw in keywords:
            if len(kw) >= 6 and kw in title_lower:
                score += 5
        return round(score, 2)

    def _build_query_variations(self, terms: list[str]) -> list[str]:
        variations = list(terms)
        n = len(terms)
        if n > 1:
            for i in range(n - 1):
                variations.append(f"{terms[i]} {terms[i+1]}")
            if n > 2:
                for i in range(n - 2):
                    variations.append(f"{terms[i]} {terms[i+1]} {terms[i+2]}")
        # unique preserve order
        seen = set()
        out = []
        for v in variations:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _extract_keywords(self, title: str) -> list[str]:
        stop = {"video", "viral", "full", "hd", "mp4", "download", "nonton", "streaming", "bokep", "indo"}
        words = re.split(r"\s+", title.lower())
        words = [w for w in words if len(w) > 2 and w not in stop]
        return words[:4]

    def _random_words(self, count: int = 5) -> list[str]:
        pool = [
            "Viral", "TikTok", "Streaming", "Full HD", "Terbaru",
            "Indo", "Simontok", "Doodstream", "Lulustream", "Asupan",
            "Video", "Durasi Panjang", "Hot", "Trending", "Sotwe",
        ]
        random.shuffle(pool)
        return pool[: max(0, count)]

    def _remove_duplicate_titles(self, items: list) -> list:
        seen = set()
        unique = []
        for item in items:
            t = (item.get("title") or "").lower().strip()
            if not t or t in seen:
                continue
            seen.add(t)
            unique.append(item)
        return unique

    def _empty_result(self, page: int = 1) -> dict:
        return {
            "items": [],
            "total": 0,
            "total_pages": 0,
            "page": page,
            "query": "",
        }


# Singleton helper
_instance: Optional[AGC] = None


def agc() -> AGC:
    global _instance
    if _instance is None:
        _instance = AGC()
    return _instance
