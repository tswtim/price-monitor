"""Configuration for competitor price monitoring."""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
REPORTS_DIR = DATA_DIR / "reports"
USER_PRICE_FILE = DATA_DIR / "user_price.json"
DB_PATH = DATA_DIR / "prices.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ProductTarget:
    """A product to monitor from the user's catalog."""
    name: str                          # "Икра горбуши 1/120"
    weight_g: float                    # 120
    keywords: list[str] = field(default_factory=list)  # ["горбуша", "горбуши"]
    our_price_rub: Optional[float] = None  # 1590 — our retail price


@dataclass
class SiteConfig:
    """Configuration for a competitor site."""
    name: str                          # "seafood-shop.ru"
    display_name: str                  # "Икорный"
    base_url: str                      # "https://seafood-shop.ru"
    method: str                        # "json_api" | "html_lxml" | "playwright"
    catalog_url: str                   # full URL to caviar catalog
    # For json_api:
    api_url: Optional[str] = None      # "https://api.seafood-shop.ru/catalog/ikra/krasnaya_ikra/"
    # Additional:
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 30
    max_pages: int = 20  # safety limit


DEFAULT_SITES: list[SiteConfig] = [
    SiteConfig(
        name="apeti.ru",
        display_name="Apeti",
        base_url="https://apeti.ru",
        method="html_lxml",
        catalog_url="https://apeti.ru/catalog/ikra/",
        timeout_seconds=30,
        max_pages=5,
    ),
    SiteConfig(
        name="seafood-shop.ru",
        display_name="Икорный",
        base_url="https://seafood-shop.ru",
        method="json_api",
        catalog_url="https://seafood-shop.ru/catalog/ikra/krasnaya_ikra/",
        api_url="https://api.seafood-shop.ru/catalog/ikra/krasnaya_ikra/",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        timeout_seconds=30,
        max_pages=10,
    ),
    SiteConfig(
        name="delikateska.ru",
        display_name="Деликатеска",
        base_url="https://www.delikateska.ru",
        method="playwright",
        catalog_url="https://www.delikateska.ru/catalog/ikra",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        timeout_seconds=45,
        max_pages=1,
    ),
]

DEFAULT_TARGETS: list[ProductTarget] = [
    ProductTarget(
        name="Икра горбуши 120 г",
        weight_g=120,
        keywords=["горбуш"],
        our_price_rub=1590,
    ),
]


def load_user_price() -> Optional[float]:
    """Load the user's own price from data/user_price.json."""
    if USER_PRICE_FILE.exists():
        data = json.loads(USER_PRICE_FILE.read_text(encoding="utf-8"))
        return data.get("price_rub")
    return None
