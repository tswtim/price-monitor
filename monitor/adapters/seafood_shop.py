"""seafood-shop.ru adapter — direct JSON API, no browser needed.

Products are returned at content.catalog.items from the API endpoint.
Pagination via ?page=N with pageAll metadata.
"""

import httpx
from urllib.parse import urljoin

from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams, safe_int_price


class SeafoodShopAdapter(BaseAdapter):
    """Fetch products from seafood-shop.ru via JSON API.

    API: https://api.seafood-shop.ru/catalog/ikra/krasnaya_ikra/?page=N
    Response: { "content": { "catalog": { "items": [...], "pageAll": 2, "itemsAll": 32 } } }
    """

    def fetch(self) -> list[RawProduct]:
        api_url = self.config.api_url or "https://api.seafood-shop.ru/catalog/ikra/krasnaya_ikra/"
        page = 1
        page_all = 1
        all_products = []

        client = httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                **self.headers,
            },
            follow_redirects=True,
        )

        while page <= min(page_all + 1, self.config.max_pages):
            url = f"{api_url.rstrip('/')}/?page={page}"
            data = self._get_json(client, url)
            if not data:
                break

            # Extract pagination info
            catalog = data.get("content", {}).get("catalog", {})
            items = catalog.get("items", [])
            page_all = catalog.get("pageAll", page_all)

            if not items and page == 1:
                # Try alternative structure — items might be at top level
                items = data.get("items", [])

            if not items:
                break

            all_products.extend(self._parse_items(items))
            page += 1
            self._sleep(0.3, 0.8)

        return all_products

    def _get_json(self, client: httpx.Client, url: str) -> dict | None:
        """Fetch JSON from the API."""
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"  [seafood-shop.ru] API error on {url}: {e}")
            return None

    def _parse_items(self, items: list[dict]) -> list[RawProduct]:
        """Parse product items from the API response."""
        products = []

        for item in items:
            title = item.get("title", "")
            if not title:
                continue

            code = item.get("code", "")
            article = item.get("article", "")

            # Weight from weightFormat
            weight_g = None
            weight_format = item.get("weightFormat", "")
            if weight_format:
                weight_g = extract_weight_grams(weight_format)

            # If not found, try from title
            if weight_g is None:
                weight_g = extract_weight_grams(title)

            # Price
            prices = item.get("prices", {})
            price_rub = float(prices.get("priceSource", 0))
            price_formatted = prices.get("price", "")

            if price_rub <= 0 and price_formatted:
                p = safe_int_price(price_formatted)
                if p:
                    price_rub = float(p)

            # Availability
            available = item.get("available", "")
            in_stock = available not in ("Нет", "0", "")

            # URL
            link = item.get("link", "")
            if link and not link.startswith("http"):
                link = urljoin("https://seafood-shop.ru", link)

            # ID
            product_id = article or code or str(item.get("id", ""))

            products.append(RawProduct(
                site=self.name,
                product_id=product_id,
                name=title,
                weight_g=weight_g,
                price_rub=price_rub,
                in_stock=in_stock,
                url=link,
                raw={
                    "article": article,
                    "code": code,
                    "available": available,
                    "weightFormat": weight_format,
                    "priceFormatted": price_formatted,
                },
            ))

        return products
