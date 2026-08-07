"""delikateska.ru adapter — Playwright-based, full catalog.

delikateska.ru blocks non-browser GraphQL requests (403).
We use Playwright to navigate each category page and capture API responses.
"""

import re
import httpx
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams


class DelikateskaAdapter(BaseAdapter):
    """Fetch ALL products from delikateska.ru via Playwright.

    Strategy:
    1. Visit catalog page, capture category menu from GraphQL response
    2. For each category, navigate to its page, capture getProducts response
    """

    def fetch(self) -> list[RawProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  [delikateska.ru] Playwright not installed")
            return []

        all_products = []
        seen_ids = set()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})

            # Step 1: Get category list from main catalog
            categories = self._capture_categories(page)
            print(f"   Категорий для обхода: {len(categories)}")

            # Step 2: Visit each category page
            for i, (ident, title) in enumerate(categories):
                if i > 0:
                    page.wait_for_timeout(500)  # brief pause between categories
                try:
                    api_products = self._capture_category_products(page, ident)
                except Exception as e:
                    print(f"   [!] {title}: {e}")
                    api_products = []
                new_count = 0
                for item in api_products:
                    pid = str(item.get("id", ""))
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)

                    product = self._parse_item(item)
                    if product:
                        all_products.append(product)
                        new_count += 1

            browser.close()

        return all_products

    def _capture_categories(self, page) -> list[tuple[str, str]]:
        """Visit catalog page and capture category menu."""
        api_data = []

        def handle(response):
            if response.status == 200 and "graphql" in response.url:
                try:
                    data = response.json()
                    menu = data.get("data", {}).get("getMobileMenuTree", [])
                    if menu and len(menu) > 10:
                        api_data.append(menu)
                except Exception:
                    pass

        page.on("response", handle)
        page.goto("https://www.delikateska.ru/catalog", timeout=45000,
                  wait_until="networkidle")
        page.wait_for_timeout(3000)

        if not api_data:
            return []

        menu = api_data[-1]
        if not isinstance(menu, list):
            return []
        result = []
        for c in menu:
            if not isinstance(c, dict):
                continue
            ident = c.get("identify", "") or ""
            title = c.get("title", "") or ""
            if ident:
                result.append((ident, title))
        return result

    def _capture_category_products(self, page, ident: str) -> list[dict]:
        """Navigate to a category page and capture product API response."""
        api_items = []

        def handle(response):
            if response.status == 200 and "graphql" in response.url:
                try:
                    data = response.json()
                    products = data.get("data", {}).get("getProducts", {})
                    items = products.get("catalogItems", [])
                    if items:
                        api_items.extend(items)
                except Exception:
                    pass

        page.on("response", handle)
        page.goto(f"https://www.delikateska.ru/catalog/{ident}",
                  timeout=30000, wait_until="networkidle")
        page.wait_for_timeout(2000)

        return api_items

    def _parse_item(self, item: dict) -> RawProduct | None:
        title = item.get("title", "")
        if not title:
            return None

        # Price
        price_rub = float(item.get("currentPriceField", 0) or
                         item.get("price_retail", 0))
        if price_rub <= 0:
            return None

        # Weight
        weight_g = None
        symbol_price = item.get("symbolPriceField", "")
        if symbol_price:
            match = re.search(r"за\s+([\d.,]+)\s*(кг|г)", symbol_price, re.IGNORECASE)
            if match:
                value = float(match.group(1).replace(",", "."))
                unit = match.group(2).lower()
                weight_g = round(value * 1000, 1) if unit in ("кг", "kg") else value
        if weight_g is None:
            weight_g = extract_weight_grams(title)

        # ID and URL
        product_id = str(item.get("id", ""))
        rubric = item.get("mainRootRubric", {})
        rubric_ident = rubric.get("identify", "")
        url = f"https://www.delikateska.ru/catalog/{rubric_ident}/element/{product_id}/" if rubric_ident and product_id else ""

        # Stock
        gds_count = item.get("gds_count", 0)
        in_stock = gds_count > 0

        return RawProduct(
            site=self.name,
            product_id=product_id,
            name=title,
            weight_g=weight_g,
            price_rub=price_rub,
            in_stock=in_stock,
            url=url,
            raw={
                "subtitle": item.get("subtitle", ""),
                "symbolPriceField": symbol_price,
            },
        )
