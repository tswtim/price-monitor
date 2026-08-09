"""globus.ru adapter — Next.js _next/data API for fast pagination."""

import re
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams


class GlobusAdapter(BaseAdapter):
    """Scrape online.globus.ru (Глобус Красногорск).

    Uses Playwright to get cookies + categories once, then
    fast _next/data API for paginated product extraction.
    """

    def fetch(self) -> list[RawProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  [globus.ru] Playwright not installed")
            return []

        all_products = []
        seen_ids = set()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_page()
            page.set_viewport_size({"width": 1400, "height": 900})

            # Load main page once
            page.goto("https://online.globus.ru/", timeout=30000, wait_until="commit")
            page.wait_for_timeout(3000)
            self._setup_store(page)

            # Get categories and buildId
            categories = self._get_categories(page)
            build_id = page.evaluate("() => window.__NEXT_DATA__?.buildId || '4qElVd8vtBjxsFeRp-pK3'")
            print(f"   Категорий: {len(categories)}")

            # Process each category via fast _next/data API
            for i, (cat_name, cat_url) in enumerate(categories):
                # Extract category slug from URL
                cat_slug = cat_url.rstrip("/").split("/")[-1]

                page_num = 1
                cat_items = []
                cat_total = 0

                while True:
                    # Use fast _next/data API instead of browser navigation
                    data_url = f"https://online.globus.ru/_next/data/{build_id}/catalog/{cat_slug}.json"
                    if page_num > 1:
                        data_url += f"?page={page_num}"

                    result = page.evaluate(f"""
                        async () => {{
                            const resp = await fetch('{data_url}');
                            if (!resp.ok) return null;
                            const data = await resp.json();
                            const queries = data?.pageProps?.dehydratedState?.queries || [];
                            for (const q of queries) {{
                                if (q?.queryKey?.[0] === 'catalog-product-list') {{
                                    const pages = q?.state?.data?.pages || [];
                                    const items = [];
                                    let total = 0;
                                    for (const p of pages) {{
                                        const prods = p?.data?.products?.items || [];
                                        items.push(...prods);
                                        total = p?.data?.products?.pagination?.total || total;
                                    }}
                                    return {{items, total}};
                                }}
                            }}
                            return null;
                        }}
                    """)

                    if not result or not result.get("items"):
                        break

                    cat_items.extend(result["items"])
                    if cat_total == 0:
                        cat_total = result.get("total", 0)

                    if len(cat_items) >= cat_total or len(result["items"]) == 0:
                        break

                    page_num += 1
                    if page_num > 50:
                        break

                # Parse products from this category
                for item in cat_items:
                    pid = str(item.get("id", ""))
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)
                    product = self._parse_product(item)
                    if product:
                        all_products.append(product)

                if (i + 1) % 10 == 0:
                    print(f"   {i+1}/{len(categories)} категорий, {len(all_products)} товаров")

            browser.close()

        return all_products

    def _setup_store(self, page) -> None:
        try:
            page.click('[role="dialog"] :text("Гипермаркет")', timeout=3000)
            page.wait_for_timeout(1000)
        except Exception:
            pass
        try:
            page.evaluate("""() => {
                const btns = [...document.querySelectorAll('button')];
                const daBtn = btns.find(b => b.innerText?.trim() === 'Да');
                if (daBtn) daBtn.click();
            }""")
            page.wait_for_timeout(1500)
        except Exception:
            pass

    def _get_categories(self, page) -> list[tuple[str, str]]:
        cats = page.evaluate("""() => {
            const cats = [];
            const seen = new Set();
            const links = [...document.querySelectorAll('a[href*="/catalog/"]')];
            for (const a of links) {
                const name = a.innerText?.trim();
                const href = a.href;
                if (name && href && name.length > 2 && !seen.has(href) && !href.includes('/404')) {
                    seen.add(href);
                    cats.push([name, href]);
                }
            }
            return cats;
        }""")
        return [(c[0], c[1]) for c in cats if c[1]]

    def _parse_product(self, item: dict) -> RawProduct | None:
        name = f"{item.get('name_optional', '')} {item.get('name_required', '')}".strip()
        if not name:
            return None

        order_price = item.get("order_price", 0)
        if order_price <= 0:
            return None
        price_rub = order_price / 100

        if not item.get("active", True) or item.get("state_id") != 1:
            return None

        weight_g = None
        weight_text = item.get("weight_text", "") or ""
        if weight_text:
            match = re.search(r'([\d.,]+)\s*(г|кг)', weight_text)
            if match:
                val = float(match.group(1).replace(",", "."))
                weight_g = val * 1000 if match.group(2) == "кг" else val
        if weight_g is None:
            weight_g = extract_weight_grams(name)

        pid = str(item.get("id", ""))
        # Use the product's own URL field, or construct from id
        product_url = item.get("url", "") or ""
        if product_url and not product_url.startswith("http"):
            url = f"https://online.globus.ru{product_url}"
        elif product_url:
            url = product_url
        else:
            url = f"https://online.globus.ru/products/{pid}/" if pid else ""
        stock = item.get("state_id") == 1 and item.get("active", False)

        return RawProduct(
            site=self.name,
            product_id=pid,
            name=name,
            weight_g=weight_g,
            price_rub=price_rub,
            in_stock=stock,
            url=url,
            raw={"name_optional": item.get("name_optional", ""),
                 "name_required": item.get("name_required", "")},
        )
