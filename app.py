"""
AGC Full Stack — Python (FastAPI + Jinja2)
Fitur setara AGC Simple PHP, core atomic cache + parallel HTTP.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import CONFIG, BASE_DIR, TEMPLATES_DIR, STATIC_DIR, get as cfg
from agc import agc
from db import log_download, list_recent_videos, video_count
from helpers import (
    site_url,
    slugify,
    search_permalink,
    download_permalink,
    download_short,
    category_permalink,
    seo_replace,
    badword_redirect,
    gibberish_check,
    dmca_blocked,
    get_recent_searches,
    get_video_category,
    get_paging,
)

app = FastAPI(title=cfg("site_name", "AGC"), docs_url=None, redoc_url=None)

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def ctx(request: Request, **extra):
    """Common template context."""
    b = base_url(request)
    return {
        "request": request,
        "config": CONFIG,
        "site_name": cfg("site_name"),
        "site_tagline": cfg("site_tagline"),
        "theme_color": cfg("theme_color", "#f44336"),
        "base": b,
        "search_url": lambda q: search_permalink(q, b),
        "download_url": lambda t, i: download_permalink(t, i, b),
        "category_url": lambda c: category_permalink(c, b),
        **extra,
    }


# -------------------- Routes --------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, page: int = Query(1, ge=1)):
    a = agc()
    top = a.get_youtube_top_videos()
    playlist = a.get_youtube_playlist()
    seen = set()
    videos = []
    for v in (top or []) + (playlist or []):
        vid = v.get("id") or ""
        if not vid or vid in seen:
            continue
        seen.add(vid)
        videos.append(v)
    if page > 1:
        videos = a.latest_videos(page, cfg("per_page", 20))

    recent = get_recent_searches()
    popular = videos[:5]
    slider = videos[:6]
    b = base_url(request)

    page_title = seo_replace(cfg("home_title"), {
        "site_name": cfg("site_name"), "domain": request.url.hostname or "",
    })
    if page > 1:
        page_title += f" Halaman {page}"
    meta_desc = seo_replace(cfg("home_description"), {
        "site_name": cfg("site_name"), "domain": request.url.hostname or "",
    })
    robots = "noindex, follow" if page > 1 else cfg("home_robots")
    canonical = b + (f"/page/{page}" if page > 1 else "/")

    item_list = []
    for i, v in enumerate(videos[:10]):
        item_list.append({
            "@type": "ListItem",
            "position": i + 1,
            "item": {
                "@type": "VideoObject",
                "name": v.get("title", ""),
                "thumbnailUrl": [v.get("thumb") or ""],
                "contentUrl": download_permalink(v.get("title", ""), v.get("id", ""), b),
            },
        })
    json_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "url": b,
                "name": cfg("site_name"),
                "potentialAction": {
                    "@type": "SearchAction",
                    "target": b + "/f/{search_term_string}",
                    "query-input": "required name=search_term_string",
                },
            },
            {"@type": "ItemList", "name": "Latest Videos", "itemListElement": item_list},
        ],
    }

    return templates.TemplateResponse("home.html", ctx(
        request,
        page_title=page_title,
        meta_desc=meta_desc,
        meta_robots=robots,
        canonical=canonical,
        og_image=(videos[0].get("thumb") if videos else ""),
        body_class="home blog",
        json_ld=json.dumps(json_ld, ensure_ascii=False),
        videos=videos,
        slider=slider,
        popular=popular,
        recent=recent,
        page=page,
        paging=get_paging(page, max(page + 1, 2), b + "/"),
    ))


@app.get("/page/{page}", response_class=HTMLResponse)
async def home_page(request: Request, page: int):
    return await home(request, page=max(1, page))


@app.get("/f/{query:path}", response_class=HTMLResponse)
async def search(request: Request, query: str, page: int = Query(1, ge=1)):
    # Support /f/query/page/N
    m = re.search(r"/page/(\d+)/?$", "/" + query)
    if m:
        page = int(m.group(1))
        query = re.sub(r"/page/\d+/?$", "", query).strip("/")

    q = query.replace("-", " ").strip()
    b = base_url(request)

    redir = badword_redirect(q, b)
    if redir:
        return RedirectResponse(redir, status_code=301)
    if gibberish_check(q):
        return RedirectResponse(b + "/", status_code=302)

    can = search_permalink(q, b) + (f"/page/{page}" if page > 1 else "")
    if dmca_blocked(can):
        return RedirectResponse(b + "/", status_code=302)

    result = agc().search(q, page)
    items = result.get("items") or []
    total = result.get("total") or 0
    total_pages = result.get("total_pages") or 1
    total_fmt = f"{total:,}".replace(",", ".")

    page_title = seo_replace(cfg("search_title"), {
        "query": q, "total": total_fmt, "site_name": cfg("site_name"),
    })
    if page > 1:
        page_title += f" Halaman {page}"
    meta_desc = seo_replace(cfg("search_description"), {
        "query": q, "total": total_fmt, "site_name": cfg("site_name"),
    })
    robots = "noindex, follow" if page > 1 else cfg("search_robots")
    fafifu = seo_replace(cfg("fafifu_search"), {
        "query": q, "total": total_fmt,
        "site_name": cfg("site_name"), "domain": request.url.hostname or "",
    })
    h1 = seo_replace(cfg("search_page_title"), {"query": q, "total": total_fmt})
    base_search = search_permalink(q, b)

    return templates.TemplateResponse("search.html", ctx(
        request,
        page_title=page_title,
        meta_desc=meta_desc,
        meta_robots=robots,
        canonical=can,
        og_image=(items[0].get("thumb") if items else ""),
        body_class="search search-results",
        query=q,
        h1=h1,
        fafifu=fafifu,
        total_fmt=total_fmt,
        items=items,
        popular=items[:5],
        page=page,
        total_pages=total_pages,
        paging=get_paging(page, total_pages, base_search),
    ))


@app.get("/e/{slug_id:path}", response_class=HTMLResponse)
async def download(request: Request, slug_id: str):
    b = base_url(request)
    # e/slug-id or e/id
    parts = slug_id.rsplit("-", 1)
    if len(parts) == 2 and parts[1]:
        vid = parts[1]
        slug = parts[0]
    else:
        vid = slug_id
        slug = ""

    can = download_permalink("x", vid, b)  # temp; fix after fetch
    if dmca_blocked(str(request.url).split("?")[0]):
        return RedirectResponse(b + "/", status_code=302)

    video = agc().get_video(vid)
    if not video:
        return RedirectResponse(b + "/", status_code=302)

    expected = download_permalink(video["title"], video["id"], b)
    current = str(request.url).split("?")[0].rstrip("/")
    if slug and current != expected.rstrip("/"):
        # short link e/id is allowed without redirect
        if "-" in slug_id:
            return RedirectResponse(expected, status_code=301)

    # Catat akses /e/ ke SQLite
    try:
        client = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")
        log_download(
            video["id"],
            slug=slug,
            path=str(request.url.path),
            ip=client or "",
            user_agent=ua,
        )
    except Exception:
        pass

    related = agc().related(video["title"], video["id"], 8)
    category = get_video_category(video["title"])
    clean = re.sub(r"\b(video|viral)\b", "", video["title"], flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip() or video["title"]

    page_title = seo_replace(cfg("download_title"), {
        "title": clean, "size": video.get("size") or "HD",
        "duration": video.get("duration", ""), "site_name": cfg("site_name"),
        "domain": request.url.hostname or "",
    })
    meta_desc = seo_replace(cfg("download_description"), {
        "title": clean, "size": video.get("size") or "HD",
        "duration": video.get("duration", ""), "site_name": cfg("site_name"),
        "domain": request.url.hostname or "",
    })
    fafifu = seo_replace(cfg("fafifu_download"), {
        "title": video["title"], "size": video.get("size") or "HD",
        "duration": video.get("duration", ""), "domain": request.url.hostname or "",
    })
    h1 = seo_replace(cfg("download_page_title"), {
        "title": clean, "site_name": cfg("site_name"),
        "domain": request.url.hostname or "",
    })

    json_ld = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": video["title"],
        "description": meta_desc,
        "thumbnailUrl": [video.get("thumb") or ""],
        "duration": "PT" + (video.get("duration") or "0:00").replace(":", "M") + "S",
        "contentUrl": expected,
        "embedUrl": video.get("embed") or "",
    }

    return templates.TemplateResponse("download.html", ctx(
        request,
        page_title=page_title,
        meta_desc=meta_desc,
        meta_robots=cfg("download_robots"),
        canonical=expected,
        og_image=video.get("thumb") or "",
        body_class="single single-video",
        json_ld=json.dumps(json_ld, ensure_ascii=False),
        video=video,
        h1=h1,
        fafifu=fafifu,
        category=category,
        related=related,
    ))


@app.get("/categories", response_class=HTMLResponse)
@app.get("/categories/{slug}", response_class=HTMLResponse)
async def categories(request: Request, slug: str = "", page: int = Query(1, ge=1)):
    b = base_url(request)
    cats = ["Viral", "TikTok", "Indo", "Asupan", "SMA", "Live", "Twitter"]
    name = slug.replace("-", " ").title() if slug else "Semua Kategori"
    items, total_pages = [], 1
    if slug:
        result = agc().search(name, page)
        items = result.get("items") or []
        total_pages = result.get("total_pages") or 1

    return templates.TemplateResponse("category.html", ctx(
        request,
        page_title=f"{name} - {cfg('site_name')}",
        meta_desc=f"Kumpulan video kategori {name}",
        meta_robots="noindex, follow" if page > 1 else "index, follow",
        canonical=category_permalink(name, b) if slug else b + "/categories",
        body_class="archive category",
        category_name=name,
        slug=slug,
        categories=cats,
        items=items,
        paging=get_paging(page, total_pages, category_permalink(name, b) if slug else b + "/categories"),
    ))


@app.get("/p/term-of-services", response_class=HTMLResponse)
@app.get("/tos", response_class=HTMLResponse)
async def tos(request: Request):
    b = base_url(request)
    return templates.TemplateResponse("page.html", ctx(
        request,
        page_title=f"Ketentuan Layanan - {cfg('site_name')}",
        meta_desc="Syarat dan ketentuan",
        meta_robots="noindex, follow",
        canonical=b + "/p/term-of-services",
        body_class="page page-tos",
        heading="Ketentuan Layanan (TOS)",
        body_html="""
        <p>Dengan mengakses situs ini, Anda setuju:</p>
        <ol style="margin:16px 0 16px 20px;list-style:decimal">
          <li>Konten hanya untuk pengguna 18+.</li>
          <li>Pengguna bertanggung jawab atas materi yang diunduh.</li>
          <li>Kami tidak menyimpan file video di server sendiri.</li>
          <li>Permintaan DMCA diproses sesuai prosedur.</li>
        </ol>
        """,
    ))


@app.get("/p/disclaimer", response_class=HTMLResponse)
@app.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer(request: Request):
    b = base_url(request)
    return templates.TemplateResponse("page.html", ctx(
        request,
        page_title=f"Disclaimer - {cfg('site_name')}",
        meta_desc="Disclaimer",
        meta_robots="noindex, follow",
        canonical=b + "/p/disclaimer",
        body_class="page page-disclaimer",
        heading="Disclaimer",
        body_html="""
        <p>Situs ini merupakan agregator tautan. Kami tidak menghosting file video.</p>
        <p>Konten disediakan pihak ketiga. Penghapusan hak cipta dapat diajukan via DMCA.</p>
        """,
    ))


@app.get("/ajax/suggest")
async def ajax_suggest(q: str = Query("", min_length=0)):
    q = q.strip()
    if len(q) < 2:
        return JSONResponse([])
    # Simple: return recent-like suggestions from search terms / google-like stub
    # Reuse google suggest pattern via urllib if needed; keep light
    try:
        from urllib.request import urlopen, Request
        from urllib.parse import urlencode
        import json as _json
        url = "https://suggestqueries.google.com/complete/search?" + urlencode({
            "output": "firefox", "client": "firefox",
            "hl": cfg("language", "id"), "q": q,
        })
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode())
            suggestions = data[1] if isinstance(data, list) and len(data) > 1 else []
            return JSONResponse(suggestions[:10])
    except Exception:
        return JSONResponse([])


@app.get("/sitemap.xml")
async def sitemap_index(request: Request):
    b = base_url(request)
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>{b}/sitemap/searches.xml</loc></sitemap>
</sitemapindex>'''
    return Response(content=xml, media_type="application/xml")


@app.get("/sitemap/searches.xml")
async def sitemap_searches(request: Request):
    b = base_url(request)
    recent = get_recent_searches(100)
    urls = []
    for q in recent:
        loc = search_permalink(q, b)
        urls.append(f"  <url><loc>{loc}</loc><changefreq>weekly</changefreq></url>")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls) + "\n</urlset>"
    return Response(content=xml, media_type="application/xml")


@app.get("/clear")
async def clear_cache(key: str = Query("")):
    if key != "agc-clear":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    n1 = agc().clear_search_cache()
    n2 = agc().clean_old_cache()
    return JSONResponse({"cleared_search": n1, "cleaned_old": n2})


# Form search redirect
@app.get("/search")
async def search_form(request: Request, q: str = "", s: str = ""):
    term = (q or s or "").strip()
    b = base_url(request)
    if term:
        return RedirectResponse(search_permalink(term, b), status_code=302)
    return RedirectResponse(b + "/", status_code=302)


@app.get("/api/stats")
async def api_stats():
    return JSONResponse({
        "videos_in_db": video_count(),
        "recent": list_recent_videos(10),
    })



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=cfg("host", "0.0.0.0"),
        port=int(cfg("port", 8000)),
        reload=bool(cfg("debug", False)),
    )
