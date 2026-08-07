"""seafood-shop.ru adapter — JSON API, full catalog."""

import httpx
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams, safe_int_price


class SeafoodShopAdapter(BaseAdapter):
    API_BASE = "https://api.seafood-shop.ru"

    def fetch(self) -> list[RawProduct]:
        client = httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                **self.headers,
            },
            follow_redirects=True,
        )

        # Get category tree
        data = client.get(f"{self.API_BASE}/catalog/").json()
        side = data.get("content", {}).get("sideCatalog", [])

        # Collect leaf categories
        cat_urls = []
        def walk(cats):
            for cat in cats:
                curl = cat.get("url", "")
                children = cat.get("items", [])
                if children:
                    walk(children)
                elif curl:
                    cat_urls.append(f"{self.API_BASE}{curl}")
        walk(side)

        print(f"   Категорий для обхода: {len(cat_urls)}")

        # Fetch all products from all categories
        all_products = []
        seen_ids = set()

        for cat_url in cat_urls:
            # Fresh client per category (avoid connection issues on long runs)
            cat_client = httpx.Client(
                timeout=self.timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    **self.headers,
                },
                follow_redirects=True,
            )
            page = 1
            while True:
                url = f"{cat_url}?page={page}"
                try:
                    resp = cat_client.get(url)
                    cat_data = resp.json().get("content", {}).get("catalog", {})
                except Exception:
                    break

                items = cat_data.get("items", [])
                if not items:
                    break

                for item in items:
                    pid = item.get("article") or item.get("code") or str(item.get("id", ""))
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)

                    p = self._parse_item(item)
                    if p:
                        all_products.append(p)

                pagination = cat_data.get("pagination", {})
                if page >= pagination.get("pageAll", 1):
                    break
                page += 1

        return all_products

    def _parse_item(self, item: dict) -> RawProduct | None:
        title = item.get("title", "")
        if not title:
            return None

        prices = item.get("prices", {})
        price_rub = float(prices.get("priceSource", 0))
        if price_rub <= 0:
            pf = prices.get("price", "")
            p = safe_int_price(pf)
            price_rub = float(p) if p else 0.0
        if price_rub <= 0:
            return None

        weight_g = None
        wf = item.get("weightFormat", "")
        if wf:
            weight_g = extract_weight_grams(wf)
        if weight_g is None:
            weight_g = extract_weight_grams(title)

        link = item.get("link", "")
        if link and not link.startswith("http"):
            link = f"https://seafood-shop.ru{link}"

        product_id = item.get("article") or item.get("code") or str(item.get("id", ""))
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
