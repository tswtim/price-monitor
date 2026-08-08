"""delikateska.ru adapter — Playwright cookies + direct GraphQL via page.evaluate()."""

import re
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams


class DelikateskaAdapter(BaseAdapter):
    """Full catalog via Playwright — uses page.evaluate + fetch for GraphQL.

    Avoids slow page navigation. Gets cookies from main page,
    then queries all 27 categories via in-browser fetch().
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
            # Launch browser — prefer headed (headless=False) to avoid 403 blocks.
            # On headless systems (Colab/VPS), use Xvfb virtual display.
            # If neither works, fall back to headless with anti-detection.
            launch_args = ["--no-sandbox", "--disable-setuid-sandbox",
                          "--disable-dev-shm-usage"]

            # Try headed first (needs DISPLAY or Xvfb)
            try:
                browser = pw.chromium.launch(headless=False, args=launch_args)
            except Exception:
                # Fall back to headless
                try:
                    browser = pw.chromium.launch(headless=True, args=launch_args)
                except Exception as e:
                    print(f"   [!] Chromium error: {e}")
                    return []

            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            page.evaluate("() => { Object.defineProperty(navigator, 'webdriver', { get: () => false }); }")

            # Visit main page
            page.goto("https://www.delikateska.ru/", timeout=45000,
                      wait_until="networkidle")
            page.wait_for_timeout(2000)

            # Check if blocked (403)
            body_start = page.content()[:500]
            if "403" in body_start and "Forbidden" in body_start:
                print("   [!] delikateska.ru заблокировал (403). Попробуй запустить с Xvfb:")
                print("       apt-get install -y xvfb && Xvfb :99 -screen 0 1280x1024x24 &")
                print("       export DISPLAY=:99")
                print("   Пока пропускаем этот сайт.")
                browser.close()
                return []

            # Get shop category list via Playwright network capture
            shop_cats = self._get_shop_categories(page)
            if not shop_cats:
                print("   [!] No categories found")
                browser.close()
                return []

            # Filter to categories with identify
            cat_list = []
            for ident, title in shop_cats:
                cat_list.append((ident, title))

            print(f"   Категорий для обхода: {len(cat_list)}")

            # Query each category
            for ident, title in cat_list:
                products_data = self._fetch_category(page, ident)
                for item in products_data:
                    pid = str(item.get("id", ""))
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)
                    p = self._parse_item(item)
                    if p:
                        all_products.append(p)

            browser.close()

        return all_products

    def _eval(self, page, js: str, arg=None):
        """Evaluate JavaScript in browser context."""
        try:
            if arg is not None:
                return page.evaluate(js, arg)
            return page.evaluate(js)
        except Exception:
            return None

    def _get_shop_categories(self, page) -> list[tuple[str, str]]:
        """Capture getMobileMenuTree from network responses. Returns shop categories."""
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

        for tree in menu_trees:
            if len(tree) > 20:  # Shop menu: 27 items
                cats = [(c.get("identify", ""), c.get("title", ""))
                        for c in tree
                        if isinstance(c, dict) and c.get("identify")]
                # Add any missing categories
                cats.append(("ot-shefa", "От шефа"))
                return cats

        return []

    def _fetch_category(self, page, ident: str) -> list[dict]:
        """Fetch ALL products for a category using paginated GraphQL via in-browser fetch."""
        all_items = []
        page_num = 0
        set_limit = 200

        while True:
            # Use page.evaluate with args (cleaner than f-string injection)
            result = self._eval(page, """
                async ([ident, pageNum, setLimit]) => {
                    const query = `
                        query getProducts($type: String!, $setLimit: Int, $page: Int) {
                            getProducts(type: $type, setLimit: $setLimit, page: $page) {
                                catalogItems {
                                    id title price_retail currentPriceField symbolPriceField
                                    measureItem { symbol }
                                    mainRootRubric { identify }
                                    gds_count is_not_for_sale active
                                }
                                totalCount
                            }
                        }
                    `;
                    const resp = await fetch('https://new-api.delikateska.ru/graphql', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            query,
                            variables: { type: ident, setLimit: setLimit, page: pageNum }
                        })
                    });
                    const data = await resp.json();
                    const prods = data.data.getProducts;
                    return {
                        items: prods.catalogItems || [],
                        totalCount: prods.totalCount || 0
                    };
                }
            """, [ident, page_num, set_limit])

            if not result:
                break

            items = result.get("items", [])
            total = result.get("totalCount", 0)

            all_items.extend(items)

            if not items or len(all_items) >= total:
                break

            page_num += 1
            if page_num > 30:  # safety
                break

        return all_items

    def _parse_item(self, item: dict) -> RawProduct | None:
        title = item.get("title", "")
        if not title:
            return None

        # Skip products not for sale or inactive
        if item.get("is_not_for_sale") == "Y" or item.get("active") == "N":
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
        url = f"https://www.delikateska.ru/catalog/{rubric_ident}/element/{product_id}/" if rubric_ident and product_id else ""

        # gds_count > 0 AND not marked as not-for-sale AND active
        gds = item.get("gds_count", 0)
        not_for_sale = item.get("is_not_for_sale", "") == "Y"
        is_active = item.get("active", "Y") != "N"
        in_stock = gds > 0 and not not_for_sale and is_active

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
