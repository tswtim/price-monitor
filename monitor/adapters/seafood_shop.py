"""seafood-shop.ru adapter — direct JSON API, full catalog."""

import httpx
from urllib.parse import urljoin

from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams, safe_int_price


class SeafoodShopAdapter(BaseAdapter):
    """Fetch ALL products from seafood-shop.ru via JSON API.

    Strategy:
    1. Get the category tree from /catalog/
    2. Collect all leaf category URLs
    3. Fetch products from each category
    """

    API_BASE = "https://api.seafood-shop.ru"

    def fetch(self) -> list[RawProduct]:
        client = httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                **self.headers,
            },
            follow_redirects=True,
        )

        # Step 1: get category list from main page
        category_urls = self._collect_category_urls(client)
        print(f"   Категорий для обхода: {len(category_urls)}")

        # Step 2: fetch products from each category
        all_products = []
        seen_ids = set()

        for cat_url in category_urls:
            page = 1
            while page <= self.config.max_pages:
                url = f"{cat_url}?page={page}" if "?" not in cat_url else f"{cat_url}&page={page}"
                data = self._get_json(client, url)
                if not data:
                    break

                catalog = data.get("content", {}).get("catalog", {})
                items = catalog.get("items", [])
                if not items:
                    break

                for item in items:
                    pid = item.get("article") or item.get("code") or str(item.get("id", ""))
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)

                    product = self._parse_item(item)
                    if product:
                        all_products.append(product)

                page_all = catalog.get("pageAll", 1)
                if page >= page_all:
                    break
                page += 1
                self._sleep(0.2, 0.3)

        return all_products

    def _collect_category_urls(self, client: httpx.Client) -> list[str]:
        """Walk the category tree and return all leaf category API URLs."""
        urls = []

        # Get the full catalog structure
        data = self._get_json(client, f"{self.API_BASE}/catalog/")
        if not data:
            return urls

        side_catalog = data.get("content", {}).get("sideCatalog", [])
        if not side_catalog:
            return urls

        # Recursively collect leaf categories (those with items=[])
        def walk(categories: list[dict], parent_url: str = ""):
            for cat in categories:
                cat_url = cat.get("url", "")
                full_url = f"{self.API_BASE}{cat_url}"
                children = cat.get("items", [])

                if children:
                    # Has subcategories — go deeper
                    walk(children, full_url)
                elif cat_url:
                    # Leaf category with products
                    urls.append(full_url)

        walk(side_catalog)
        return urls

    def _get_json(self, client: httpx.Client, url: str) -> dict | None:
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _parse_item(self, item: dict) -> RawProduct | None:
        title = item.get("title", "")
        if not title:
            return None

        # Price
        prices = item.get("prices", {})
        price_rub = float(prices.get("priceSource", 0))
        if price_rub <= 0:
            price_formatted = prices.get("price", "")
            p = safe_int_price(price_formatted)
            price_rub = float(p) if p else 0.0
        if price_rub <= 0:
            return None

        # Weight
        weight_g = None
        weight_format = item.get("weightFormat", "")
        if weight_format:
            weight_g = extract_weight_grams(weight_format)
        if weight_g is None:
            weight_g = extract_weight_grams(title)

        # URL
        link = item.get("link", "")
        if link and not link.startswith("http"):
            link = f"https://seafood-shop.ru{link}"

        # ID
        product_id = item.get("article") or item.get("code") or str(item.get("id", ""))

        # Stock
        available = item.get("available", "")
        in_stock = available not in ("Нет", "0", "")

        return RawProduct(
            site=self.name,
            product_id=product_id,
            name=title,
            weight_g=weight_g,
            price_rub=price_rub,
            in_stock=in_stock,
            url=link,
            raw={"article": item.get("article", ""), "code": item.get("code", "")},
        )
