"""Konfigurasi AGC Python Full"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
STORE_DIR = BASE_DIR / "store"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

CONFIG = {
    "site_name": "Simontok 2026",
    "site_tagline": "Streaming Video Viral Indo TikTok Download Link",
    "theme_color": "#f44336",
    "language": "id",

    "home_title": "%site_name% - Kumpulan Video Viral Durasi Panjang Full HD",
    "home_description": "%site_name% kumpulan video viral Indo durasi panjang Full HD. Nonton mudah, sekali play langsung jalan.",
    "home_robots": "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1",

    "search_title": "Kumpulan %total% Video %query% | %site_name%",
    "search_description": "Nonton dan download %total% kumpulan video %query% viral gratis terbaru. Kualitas Full HD terbaik.",
    "search_robots": "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1",
    "fafifu_search": "Nonton %total% Video Indonesia Viral Terbaru di %site_name%. Anda dapat menonton dan mengunduh kumpulan video %query% viral secara gratis dengan mudah dan cepat di %domain%.",

    "download_title": "Video %title% Viral %size% | %site_name%",
    "download_description": "Nonton dan download video viral %title% MP4 gratis. Ukuran %size%, durasi %duration% di %domain%.",
    "download_robots": "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1",
    "fafifu_download": "Unduh atau nonton streaming video %title% gratis dalam format MP4 dengan resolusi terbaik di %domain%.",

    "search_page_title": "Kumpulan %total% Video %query% Viral Terbaru",
    "download_page_title": "Video %title% Lagi Viral",

    "search_permalink": "f/{query}",
    "download_permalink": "e/{slug}-{id}",
    "download_short": "e/{id}",
    "category_permalink": "categories/{slug}",
    "tos_permalink": "p/term-of-services",
    "disclaimer_permalink": "p/disclaimer",

    "dood_api_key": os.environ.get("DOOD_API_KEY", ""),
    "lulu_api_key": os.environ.get("LULU_API_KEY", ""),

    "enable_cache": True,
    "cache_search": 604800,
    "cache_video": 2592000,
    "cache_list": 86400,
    "cache_api": 600,
    "cache_title": 2592000,
    "cache_suggest": 604800,

    "per_page": 20,
    "search_per_page": 50,

    "recent_limit": 12,
    "recent_searches_limit": 5000,
    "save_recent_searches": True,

    "enable_badword_filter": True,
    "enable_dmca": True,
    "enable_gibberish": True,

    # SQLite persistence untuk /e/slug-ID
    "enable_sqlite": True,
    "enable_download_log": True,
    "sqlite_prefer_db": True,  # baca DB dulu sebelum API jika masih fresh

    "http_timeout": 12,
    "http_connect_timeout": 5,
    "max_workers": 6,

    "host": "0.0.0.0",
    "port": 8000,
    "debug": False,
}


def get(key: str, default=None):
    return CONFIG.get(key, default)
