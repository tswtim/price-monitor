"""delikateska.ru adapter — GraphQL API (no browser needed).

Products are available via new-api.delikateska.ru/graphql.
Discovered through Playwright network inspection.
"""

import re
import httpx
from urllib.parse import urljoin

from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams


# GraphQL query captured from the React app
GRAPHQL_QUERY = """
query getProducts(
  $offset: Int
  $page: Int
  $setLimit: Int
  $setOrder: String
  $type: String
  $group: String
  $isExpress: Boolean
  $specOfferFirst: Boolean
  $specOffer: String
  $groups: [String!]
  $menuStoreId: Int
) {
  getProducts(
    offset: $offset
    page: $page
    setLimit: $setLimit
    setOrder: $setOrder
    type: $type
    group: $group
    specOfferFirst: $specOfferFirst
    specOffer: $specOffer
    isExpress: $isExpress
    groups: $groups
    menuStoreId: $menuStoreId
  ) {
    catalogItems {
      id
      title
      subtitle
      price_retail
      currentPriceField
      symbolPriceField
      measure_id
      measureItem {
        weight
        symbol
      }
      mainRootRubric {
        id
        title
        identify
      }
      is_not_for_sale
    }
    totalCount
    catalogSeo {
      lowPrice
      highPrice
    }
    rubricTitle
    rubricMenuTree {
      id
      title
      identify
    }
  }
}
"""


class DelikateskaAdapter(BaseAdapter):
    """Fetch products from delikateska.ru via GraphQL API.

    Uses new-api.delikateska.ru/graphql with product query.
    No browser required when API is accessible.
    """

    GRAPHQL_URL = "https://new-api.delikateska.ru/graphql"

    def fetch(self) -> list[RawProduct]:
        """Fetch products via GraphQL API, with Playwright fallback."""
        # --- Step 1: Try direct GraphQL ---
        products = self._fetch_graphql()
        if products:
            return products

        # --- Step 2: Playwright fallback (captures API responses) ---
        print("  [delikateska.ru] Direct GraphQL failed, trying Playwright...")
        return self._fetch_playwright_graphql()

    def _fetch_graphql(self) -> list[RawProduct]:
        """Call the GraphQL API directly with httpx."""
        client = httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Content-Type": "application/json",
                "Origin": "https://www.delikateska.ru",
                "Referer": "https://www.delikateska.ru/",
                **self.headers,
            },
            follow_redirects=True,
        )

        all_products = []
        page = 0
        set_limit = 48  # Max reasonable page size

        # Category identifier from the catalog URL
        category = self.config.catalog_url.rstrip("/").split("/")[-1] if self.config.catalog_url else "ikra"
        # Note: delikateska API requires a category — full catalog search not available.
        # We scrape specific categories via config. Default is "ikra".
        # For broader coverage, add more SiteConfig entries with different catalog URLs.

        while page < self.config.max_pages:
            variables = {
                "page": page,
                "setLimit": set_limit,
                "setOrder": "default",
                "type": category,
            }

            body = {
                "query": GRAPHQL_QUERY,
                "variables": variables,
            }

            try:
                resp = client.post(self.GRAPHQL_URL, json=body)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                if page == 0:
                    print(f"  [delikateska.ru] GraphQL error: {e}")
                break

            products_data = data.get("data", {}).get("getProducts", {})
            items = products_data.get("catalogItems", [])
            total_count = products_data.get("totalCount", 0)

            if not items:
                break

            parsed = self._parse_catalog_items(items)
            all_products.extend(parsed)

            if not total_count or len(all_products) >= total_count:
                break

            page += 1
            self._sleep(0.3, 0.8)

        return all_products

    def _parse_catalog_items(self, items: list[dict]) -> list[RawProduct]:
        """Parse catalogItems from GraphQL response into RawProduct."""
        products = []

        for item in items:
            title = item.get("title", "")
            if not title:
                continue

            # Skip items not for sale
            if item.get("is_not_for_sale") == "Y":
                continue

            # --- Price ---
            # Use current (discounted) price if available, else retail
            price_rub = float(item.get("currentPriceField", 0) or item.get("price_retail", 0))
            if price_rub <= 0:
                continue

            # --- Weight from symbolPriceField ---
            # Format: "цена за 0.12 кг" → 120g
            weight_g = None
            symbol_price = item.get("symbolPriceField", "")
            if symbol_price:
                # "цена за 0.12 кг"
                match = re.search(r"за\s+([\d.,]+)\s*(кг|г|kg|g)", symbol_price, re.IGNORECASE)
                if match:
                    value = float(match.group(1).replace(",", "."))
                    unit = match.group(2).lower()
                    if unit in ("кг", "kg"):
                        weight_g = round(value * 1000, 1)
                    else:
                        weight_g = value

            # Fallback: extract from title
            if weight_g is None:
                weight_g = extract_weight_grams(title)

            # --- Product ID ---
            product_id = str(item.get("id", ""))

            # --- URL ---
            url = ""
            rubric = item.get("mainRootRubric", {})
            rubric_id = rubric.get("identify", "")
            if rubric_id and product_id:
                url = f"https://www.delikateska.ru/catalog/{rubric_id}/element/{product_id}/"

            # --- Stock ---
            in_stock = item.get("gds_count", 0) > 0 if "gds_count" in item else True

            products.append(RawProduct(
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
                    "price_retail": item.get("price_retail"),
                    "currentPriceField": item.get("currentPriceField"),
                    "rubric": rubric.get("title", ""),
                },
            ))

        return products

    # --- Playwright fallback ---

    def _fetch_playwright_graphql(self) -> list[RawProduct]:
        """Use Playwright to capture GraphQL responses (visible browser)."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  [delikateska.ru] Playwright not installed. Install with:")
            print("    pip install playwright && playwright install chromium")
            return []

        products = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=False)
                page = browser.new_page()
                page.set_viewport_size({"width": 1280, "height": 800})

                # Collect API responses
                api_products = []

                def handle_response(response):
                    if response.status == 200 and "graphql" in response.url:
                        try:
                            data = response.json()
                            items = (
                                data.get("data", {})
                                .get("getProducts", {})
                                .get("catalogItems", [])
                            )
                            if items:
                                api_products.extend(items)
                        except Exception:
                            pass

                page.on("response", handle_response)
                page.goto(
                    self.config.catalog_url,
                    timeout=self.timeout * 1000,
                    wait_until="networkidle",
                )
                page.wait_for_timeout(5000)
                browser.close()

                if api_products:
                    # Deduplicate
                    seen = set()
                    unique = []
                    for item in api_products:
                        pid = item.get("id")
                        if pid and pid in seen:
                            continue
                        if pid:
                            seen.add(pid)
                        unique.append(item)

                    products = self._parse_catalog_items(unique)

        except Exception as e:
            print(f"  [delikateska.ru] Playwright error: {e}")

        return products
