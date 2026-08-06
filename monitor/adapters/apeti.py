"""apeti.ru adapter — server-rendered Bitrix site, HTML scraping with lxml."""

import re
import httpx
from lxml import etree
from urllib.parse import urljoin

from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams, safe_int_price


class ApetiAdapter(BaseAdapter):
    """Scrape apeti.ru/catalog/ikra/ using HTML parsing.

    The site is Bitrix-based with server-rendered product grids.
    Products are in div.products-flex-item elements.
    """

    def fetch(self) -> list[RawProduct]:
        products = []
        page = 1

        while page <= self.config.max_pages:
            url = self._page_url(page)
            html = self._get_html(url)
            if not html:
                break

            page_products = self._parse_products(html)
            if not page_products:
                break

            products.extend(page_products)
            page += 1
            self._sleep(0.5, 1.0)

        return products

    def _page_url(self, page: int) -> str:
        if page == 1:
            return self.config.catalog_url
        return f"{self.config.catalog_url}?PAGEN_1={page}"

    def _get_html(self, url: str) -> str | None:
        """Fetch HTML from the site."""
        try:
            client = httpx.Client(
                timeout=self.timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    **self.headers,
                },
                follow_redirects=True,
            )
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"  [apeti.ru] HTTP error on {url}: {e}")
            return None

    def _parse_products(self, html: str) -> list[RawProduct]:
        """Parse product items from the HTML page."""
        try:
            tree = etree.HTML(html)
        except Exception:
            return []

        items = tree.xpath('//div[contains(@class, "products-flex-item")]')
        products = []

        for item in items:
            raw = self._parse_item(item)
            if raw and raw.name and raw.price_rub > 0:
                products.append(raw)

        return products

    def _parse_item(self, item) -> RawProduct | None:
        """Parse a single product item element.

        apeti.ru structure:
          - First long text node = product description
          - .product-name-additional = price/kg + brand + article
          - .product-price-block = total price
          - a[data-quantity] = weight in kg
          - a[data-product-id] = product ID
          - onclick handler = brand name
        """
        # --- Collect all text nodes ---
        all_texts = []
        for text_node in item.xpath('.//text()'):
            t = text_node.strip()
            if t and len(t) > 3:
                all_texts.append(t)

        # --- Product description: longest meaningful text ---
        # Skip price-like texts and article numbers
        description = ""
        article = ""
        for t in all_texts:
            if re.match(r'^\d[\d\s]*\s*(?:р|руб|₽)', t):
                continue  # Price text
            if re.match(r'^Арт[.:]', t):
                article = re.sub(r'^Арт[.:]\s*', '', t)
                continue
            if re.match(r'^[A-Z0-9-]{5,}$', t):
                article = t if not article else article
                continue
            if len(t) > len(description) and not re.match(r'^\d+[\d\s]*\s*(?:р/кг|р|руб)', t):
                description = t

        # --- Name: combine description with article if available ---
        name = description

        # If no good description found, try product-name-additional
        if not name:
            desc_els = item.xpath('.//*[contains(@class, "product-name-additional")]//text()')
            name = " ".join(t.strip() for t in desc_els if t.strip())

        if not name:
            return None

        # --- Article from .articul class ---
        if not article:
            articul_el = item.xpath('.//*[contains(@class, "articul")]//text()')
            if articul_el:
                article = articul_el[0].strip()
                article = re.sub(r"^.*:\s*", "", article)

        # --- Weight from data-quantity (kg → g) ---
        weight_g = None
        quantity_el = item.xpath('.//a[@data-quantity]')
        if quantity_el:
            qty = quantity_el[0].get("data-quantity", "")
            try:
                qty_val = float(qty)
                # data-quantity == 1 means "per piece", not 1 kg
                # Only use if < 1 (sub-kg weights like 0.1, 0.2, 0.5)
                if qty_val < 1.0:
                    weight_g = round(qty_val * 1000, 1)
            except (ValueError, TypeError):
                pass

        # Try to extract weight from description
        if weight_g is None:
            weight_g = extract_weight_grams(name)

        # Try weight from all text nodes
        if weight_g is None:
            for t in all_texts:
                w = extract_weight_grams(t)
                if w:
                    weight_g = w
                    break

        # --- Price ---
        price_rub = 0.0
        price_per_kg_raw = None

        # Price per kg (like "14850 р/кг")
        for t in all_texts:
            match = re.search(r"(\d[\d\s]*)\s*р/кг", t)
            if match:
                price_per_kg_raw = float(match.group(1).replace(" ", ""))
                # If we have weight, compute total price
                if weight_g and weight_g > 0:
                    price_rub = round(price_per_kg_raw * weight_g / 1000, 2)
                break

        # Total price from text nodes
        if price_rub == 0.0:
            for t in all_texts:
                # Skip weight texts (containing г, гр, кг)
                if re.search(r'\d+\s*(?:г|гр|грамм|кг)\b', t, re.IGNORECASE):
                    continue
                # Skip price-per-kg texts
                if 'р/кг' in t:
                    continue
                # Skip article-like texts
                if re.match(r'^[A-Z0-9-]{4,}$', t):
                    continue
                # Match price patterns like "1 485  ₽" or "277.60  ₽"
                p = safe_int_price(t)
                if p and 200 < p < 500000:  # Caviar should be > 200 rub
                    price_rub = float(p)
                    break

        # If we have price_per_kg but no per-unit price: compute from per_kg + weight
        if price_per_kg_raw and weight_g and weight_g > 0 and price_rub == 0.0:
            price_rub = round(price_per_kg_raw * weight_g / 1000, 2)

        # If we have total price and weight but no per_kg: compute
        if price_rub > 0 and weight_g and weight_g > 0 and not price_per_kg_raw:
            price_per_kg_raw = round(price_rub / weight_g * 1000, 2)

        # Skip products without meaningful price
        if price_rub <= 0:
            return None

        # --- Product ID ---
        product_id = ""
        pid_el = item.xpath('.//a[@data-product-id]')
        if pid_el:
            product_id = pid_el[0].get("data-product-id", "")

        # --- URL ---
        url = ""
        link_el = item.xpath('.//a[contains(@class, "name")]/@href')
        if not link_el:
            link_el = item.xpath('.//a[contains(@class, "product")]/@href')
        if link_el:
            url = urljoin(self.base_url, link_el[0])

        # --- Brand ---
        brand = ""
        buy_el = item.xpath('.//a[contains(@class, "add2cart")]/@onclick')
        if buy_el:
            parts = buy_el[0].split(",")
            if len(parts) >= 4:
                brand = parts[3].strip().strip("'\" ")

        return RawProduct(
            site=self.name,
            product_id=product_id,
            name=name,
            brand=brand,
            weight_g=weight_g,
            price_rub=price_rub,
            price_per_kg_raw=price_per_kg_raw,
            url=url,
            raw={
                "article": article,
                "all_texts": all_texts,
                "price_per_kg_raw": price_per_kg_raw,
            },
        )
