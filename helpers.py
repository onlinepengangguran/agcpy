"""Helper URL, SEO, filter — pure Python."""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from config import CONFIG, STORE_DIR, get as cfg


def site_url(path: str = "", request_base: str = "") -> str:
    base = (request_base or "").rstrip("/")
    path = path.lstrip("/")
    if not base:
        return "/" + path if path else "/"
    return base + ("/" + path if path else "")


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    return text.lower().strip("-") or "n-a"


def search_permalink(query: str, base: str = "") -> str:
    q = slugify(query)
    return site_url(cfg("search_permalink", "f/{query}").format(query=q), base)


def download_permalink(title: str, vid: str, base: str = "") -> str:
    slug = slugify(title)
    return site_url(
        cfg("download_permalink", "e/{slug}-{id}").format(slug=slug, id=vid),
        base,
    )


def download_short(vid: str, base: str = "") -> str:
    return site_url(cfg("download_short", "e/{id}").format(id=vid), base)


def category_permalink(category: str, base: str = "") -> str:
    return site_url(
        cfg("category_permalink", "categories/{slug}").format(slug=slugify(category)),
        base,
    )


def seo_replace(template: str, vars: dict) -> str:
    out = template
    for k, v in vars.items():
        out = out.replace("%" + k + "%", str(v))
    return out


def get_badwords() -> list[str]:
    f = STORE_DIR / "badwords.txt"
    if not f.exists():
        return []
    raw = f.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\r\n", ",").replace("\r", ",").replace("\n", ",")
    return [w.strip() for w in raw.split(",") if w.strip()]


def filter_badwords(haystack: str, needles: list[str]) -> bool:
    if not needles:
        return False
    h = haystack.lower()
    for w in needles:
        w = w.strip()
        if not w:
            continue
        if re.search(r"\b" + re.escape(w) + r"\b", h, re.I):
            return True
    return False


def badword_redirect(query: str, base: str = "") -> Optional[str]:
    if not cfg("enable_badword_filter", True):
        return None
    q = query.lower().strip()
    if not q:
        return None
    bad = get_badwords()
    if not filter_badwords(q, bad):
        return None
    words = q.split()
    static = list(words)
    for bw in bad:
        for p in bw.lower().split():
            if p in words:
                words = [w for w in words if w != p]
    if not words:
        return site_url("", base)
    if len(words) != len(static):
        return search_permalink(" ".join(words), base)
    return None


def gibberish_check(query: str) -> bool:
    """Return True if should redirect away (gibberish)."""
    if not cfg("enable_gibberish", True):
        return False
    clean = query.strip()
    if len(clean) > 80:
        return True
    if re.search(r"[bcdfghjklmnpqrstvwxyz]{8,}", clean, re.I):
        return True
    return False


def dmca_blocked(canonical: str) -> bool:
    if not cfg("enable_dmca", True):
        return False
    f = STORE_DIR / "dmca.txt"
    if not f.exists():
        return False
    urls = [u.strip().rstrip("/") for u in f.read_text(encoding="utf-8").splitlines() if u.strip()]
    return canonical.rstrip("/") in urls


def get_recent_searches(limit: Optional[int] = None) -> list[str]:
    limit = limit or int(cfg("recent_limit", 12))
    f = STORE_DIR / "searches.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8")) or []
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for item in data:
        if len(out) >= limit:
            break
        title = item.get("title") if isinstance(item, dict) else str(item)
        if title:
            out.append(title)
    return out


def get_video_category(title: str) -> str:
    t = title.lower()
    mapping = {
        "tiktok": "TikTok", "twitter": "Twitter", "sma": "SMA",
        "mahasiswa": "Mahasiswa", "live": "Live", "asupan": "Asupan",
        "viral": "Viral", "indo": "Indo", "jepang": "Jepang",
        "korea": "Korea", "barat": "Barat",
    }
    for kw, cat in mapping.items():
        if kw in t:
            return cat
    return "Viral"


def get_paging(current: int, total_pages: int, base_url: str) -> list[dict]:
    """Return list of {label, url, active} for template."""
    if total_pages <= 1:
        return []
    items = []
    if current > 1:
        prev = current - 1
        url = base_url if prev <= 1 else base_url.rstrip("/") + f"/page/{prev}"
        items.append({"label": "← Prev", "url": url, "active": False})
    start = max(1, current - 2)
    end = min(total_pages, current + 2)
    for i in range(start, end + 1):
        url = base_url if i <= 1 else base_url.rstrip("/") + f"/page/{i}"
        items.append({"label": str(i), "url": url, "active": i == current})
    if current < total_pages:
        url = base_url.rstrip("/") + f"/page/{current + 1}"
        items.append({"label": "Next →", "url": url, "active": False})
    return items
