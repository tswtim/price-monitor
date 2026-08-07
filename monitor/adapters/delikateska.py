"""delikateska.ru adapter — Playwright with GraphQL capture."""

import re
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams


class DelikateskaAdapter(BaseAdapter):
    """Full catalog via Playwright — captures GraphQL responses.

    Visits main page for shop category list (27 categories),
    then visits each category page and captures getProducts responses.
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

            # Step 1: Visit main page, capture shop category menu (2nd getMobileMenuTree)
            shop_categories = self._get_shop_categories(page)
            if not shop_categories:
                print("   [!] Shop categories not found, falling back to catalog page")
                shop_categories = self._get_categories_from_catalog(page)

            print(f"   Категорий для обхода: {len(shop_categories)}")

            # Step 2: Visit each category, capture getProducts
            for i, (ident, title) in enumerate(shop_categories):
                try:
                    cat_products = self._get_category_products(page, ident)
                except Exception:
                    cat_products = []

                for item in cat_products:
                    pid = str(item.get("id", ""))
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)

                    product = self._parse_item(item)
                    if product:
                        all_products.append(product)

                page.wait_for_timeout(300)

            browser.close()

        return all_products

    def _get_shop_categories(self, page) -> list[tuple[str, str]]:
        """Visit main page, capture the 2nd getMobileMenuTree (shop, not restaurant)."""
        menu_trees = []

        def handle(response):
            if response.status == 200 and "graphql" in response.url:
                try:
                    data = response.json()
                    tree = data.get("data", {}).get("getMobileMenuTree", [])
                    if tree:
                        menu_trees.append(tree)
                except Exception:
                    pass

        page.on("response", handle)
        page.goto("https://www.delikateska.ru/", timeout=45000,
                  wait_until="networkidle")
        page.wait_for_timeout(3000)

        # The 2nd getMobileMenuTree has shop categories (27 items, not 16)
        for tree in menu_trees:
            if len(tree) > 20:  # Shop menu has 27, restaurant has 16
                cats = []
                for c in tree:
                    if isinstance(c, dict):
                        ident = c.get("identify", "") or ""
                        title = c.get("title", "") or ""
                        if ident:
                            cats.append((ident, title))
                if cats:
                    return cats

        return []

    def _get_categories_from_catalog(self, page) -> list[tuple[str, str]]:
        """Fallback: get categories from rubricMenuTree on catalog page."""
        menu_data = []

        def handle(response):
            if response.status == 200 and "graphql" in response.url:
                try:
                    data = response.json()
                    prods = data.get("data", {}).get("getProducts", {})
                    tree = prods.get("rubricMenuTree", [])
                    if tree:
                        menu_data.append(tree)
                except Exception:
                    pass

        page.on("response", handle)
        page.goto("https://www.delikateska.ru/catalog/ikra", timeout=45000,
                  wait_until="networkidle")
        page.wait_for_timeout(3000)

        if menu_data:
            return [(c.get("identify", ""), c.get("title", ""))
                    for c in menu_data[0]
                    if isinstance(c, dict) and c.get("identify")]

        return []

    def _get_category_products(self, page, ident: str) -> list[dict]:
        """Navigate to category page, scroll to load all products via infinite scroll."""
        api_items = []
        total_count = [0]

        def handle(response):
            if response.status == 200 and "graphql" in response.url:
                try:
                    data = response.json()
                    prods = data.get("data", {}).get("getProducts", {})
                    items = prods.get("catalogItems", [])
                    if items:
                        api_items.extend(items)
                        tc = prods.get("totalCount", 0)
                        if tc > total_count[0]:
                            total_count[0] = tc
                except Exception:
                    pass

        page.on("response", handle)
        page.goto(f"https://www.delikateska.ru/catalog/{ident}",
                  timeout=30000, wait_until="networkidle")
        page.wait_for_timeout(2000)

        # Scroll to trigger lazy loading of more products
        if total_count[0] > len(api_items):
            for _ in range(30):  # up to 30 scrolls
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(500)
                if len(api_items) >= total_count[0]:
                    break

        return api_items

    def _parse_item(self, item: dict) -> RawProduct | None:
        title = item.get("title", "")
        if not title:
            return None

        price_rub = float(item.get("currentPriceField", 0) or
                         item.get("price_retail", 0))
        if price_rub <= 0:
            return None

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

        product_id = str(item.get("id", ""))
        rubric = item.get("mainRootRubric", {}) or {}
        rubric_ident = rubric.get("identify", "")
        url = ""
        if rubric_ident and product_id:
            url = f"https://www.delikateska.ru/catalog/{rubric_ident}/element/{product_id}/"

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
            raw={"symbolPriceField": symbol_price},
        )
