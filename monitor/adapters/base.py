"""Base adapter interface for competitor site scrapers."""

import time
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawProduct:
    """Raw product data before normalization."""
    site: str
    product_id: str
    name: str
    brand: str = ""
    weight_g: Optional[float] = None
    price_rub: float = 0.0
    price_per_kg_raw: Optional[float] = None
    in_stock: bool = True
    url: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "product_id": self.product_id,
            "name": self.name,
            "brand": self.brand,
            "weight_g": self.weight_g,
            "price_rub": self.price_rub,
            "price_per_kg_raw": self.price_per_kg_raw,
            "in_stock": 1 if self.in_stock else 0,
            "url": self.url,
            "raw": self.raw,
        }


class BaseAdapter(ABC):
    """Base class for site adapters."""

    def __init__(self, config):
        self.config = config
        self.name = config.name
        self.display_name = config.display_name
        self.base_url = config.base_url
        self.timeout = config.timeout_seconds
        self.headers = dict(config.headers) if config.headers else {}

    @abstractmethod
    def fetch(self) -> list[RawProduct]:
        """Fetch products from the site. Returns list of RawProduct."""
        ...

    def _sleep(self, min_s: float = 0.5, max_s: float = 1.5) -> None:
        """Polite delay between requests."""
        time.sleep(random.uniform(min_s, max_s))
