"""apeti.ru adapter — full catalog scraping via HTML parsing."""

import re
import httpx
from lxml import etree
from urllib.parse import urljoin

from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams, safe_int_price


class ApetiAdapter(BaseAdapter):
    """Scrape apeti.ru — auto-discovers all catalog categories."""

    def fetch(self) -> list[RawProduct]:
        client = httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "ru-RU,ru;q=0.9",
                **self.headers,
            },
            follow_redirects=True,
        )

        # Step 1: get all category URLs from main catalog
        category_urls = self._discover_categories(client)
        # Always include the configured URL as fallback
        if self.config.catalog_url not in category_urls:
            category_urls.append(self.config.catalog_url)

        print(f"   Категорий для обхода: {len(category_urls)}")

        # Step 2: scrape each category
        all_products = []
        seen_ids = set()

        for cat_url in category_urls:
            page = 1
            while page <= self.config.max_pages:
                url = cat_url if page == 1 else f"{cat_url}?PAGEN_1={page}"
                html = self._get_html(client, url)
                if not html:
                    break

                page_products = self._parse_products(html)
                if not page_products:
                    break

                for p in page_products:
                    pid = p.product_id
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)
                    all_products.append(p)

                page += 1
                self._sleep(0.3, 0.5)

        return all_products

    def _discover_categories(self, client: httpx.Client) -> list[str]:
        """Get all category URLs from the main catalog page."""
        html = self._get_html(client, "https://apeti.ru/catalog/")
        if not html:
            return []

        try:
            tree = etree.HTML(html)
        except Exception:
            return []

        urls = set()
        for a in tree.xpath('//a[contains(@href, "/catalog/")]/@href'):
            href = a.strip()
            # Skip filters, auth links, non-category pages
            if any(x in href for x in ("?", "filter/", "login", "register", "element/")):
                continue
            # Only include leaf categories (not the root /catalog/)
            if href == "/catalog/":
                continue
            full = urljoin(self.base_url, href)
            urls.add(full)

        # Limit to avoid excessive scraping
        return list(urls)[:30]  # Max 30 categories

    def _get_html(self, client: httpx.Client, url: str) -> str | None:
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return None

    def _parse_products(self, html: str) -> list[RawProduct]:
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
        all_texts = []
        for text_node in item.xpath('.//text()'):
            t = text_node.strip()
            if t and len(t) > 3:
                all_texts.append(t)

        # Product description: longest meaningful text
        description = ""
        article = ""
        for t in all_texts:
            if re.match(r'^\d[\d\s]*\s*(?:р|руб)', t):
                continue
            if re.match(r'^Арт[.:]', t):
                article = re.sub(r'^Арт[.:]\s*', '', t)
                continue
            if len(t) > len(description) and not re.match(r'^\d+[\d\s]*\s*(?:р/кг|р|руб)', t):
                description = t

        name = description
        if not name:
            desc_els = item.xpath('.//*[contains(@class, "product-name-additional")]//text()')
            name = " ".join(t.strip() for t in desc_els if t.strip())
        if not name:
            return None

        # Weight
        weight_g = None
        quantity_el = item.xpath('.//a[@data-quantity]')
        if quantity_el:
            qty = quantity_el[0].get("data-quantity", "")
            try:
                qty_val = float(qty)
                if qty_val < 1.0:
                    weight_g = round(qty_val * 1000, 1)
            except (ValueError, TypeError):
                pass
        if weight_g is None:
            weight_g = extract_weight_grams(name)
        if weight_g is None:
            for t in all_texts:
                w = extract_weight_grams(t)
                if w:
                    weight_g = w
                    break

        # Price
        price_rub = 0.0
        # Price per kg first
        for t in all_texts:
            match = re.search(r"(\d[\d\s]*)\s*р/кг", t)
            if match and weight_g and weight_g > 0:
                ppk = float(match.group(1).replace(" ", ""))
                price_rub = round(ppk * weight_g / 1000, 2)
                break
        # Total price from text
        if price_rub == 0.0:
            for t in all_texts:
                if re.search(r'\d+\s*(?:г|гр|грамм|кг)\b', t, re.IGNORECASE):
                    continue
                if 'р/кг' in t:
                    continue
                p = safe_int_price(t)
                if p and 20 < p < 500000:
                    price_rub = float(p)
                    break
        if price_rub <= 0:
            return None

        # ID, URL, Brand
        product_id = ""
        pid_el = item.xpath('.//a[@data-product-id]')
        if pid_el:
            product_id = pid_el[0].get("data-product-id", "")

        url = ""
        link_el = item.xpath('.//a[contains(@class, "name")]/@href')
        if link_el:
            url = urljoin(self.base_url, link_el[0])

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
            url=url,
            raw={"article": article, "all_texts": all_texts},
        )
