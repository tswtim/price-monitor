"""apeti.ru adapter — full catalog scraping via HTML parsing."""

import re
import httpx
from lxml import etree
from urllib.parse import urljoin

from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams, safe_int_price


class ApetiAdapter(BaseAdapter):
    """Scrape apeti.ru — auto-discovers all catalog categories."""

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                **self.headers,
            },
            follow_redirects=True,
        )

    def fetch(self) -> list[RawProduct]:
        client = self._make_client()
        category_urls = self._discover_categories(client)
        if self.config.catalog_url not in category_urls:
            category_urls.append(self.config.catalog_url)
        print(f"   Категорий для обхода: {len(category_urls)}")

        all_products = []
        seen_ids = set()

        for cat_url in category_urls:
            cat_client = self._make_client()
            html = self._get_html(cat_client, cat_url)
            if not html:
                continue

            nav = self._detect_nav_num(html) or 1
            page = 1
            page_seen = set()

            while True:
                if page == 1:
                    phtml = html
                else:
                    phtml = self._get_html(cat_client, f"{cat_url}?PAGEN_{nav}={page}")
                    if not phtml or self._is_empty_page(phtml):
                        phtml = self._get_html(cat_client, f"{cat_url}?PAGEN_{nav}={page}&load=Y")
                        if not phtml:
                            break

                items = self._parse_products(phtml)
                if not items:
                    break

                new_on_page = 0
                for p in items:
                    pid = p.product_id
                    if pid and (pid in page_seen or pid in seen_ids):
                        continue
                    if pid:
                        page_seen.add(pid)
                        seen_ids.add(pid)
                    all_products.append(p)
                    new_on_page += 1

                if new_on_page == 0 and page > 1:
                    break

                page += 1
                if page > 60:
                    break

                page += 1
                if page > 60:
                    break

        return all_products

    def _discover_categories(self, client: httpx.Client) -> list[str]:
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
            if href == "/catalog/":
                continue
            if any(x in href for x in ("?", "filter/", "login", "register", "element/")):
                continue
            full = urljoin(self.base_url, href)
            urls.add(full)

        return list(urls)

    def _detect_nav_num(self, html: str) -> int | None:
        match = re.search(r'PAGEN_(\d+)', html)
        if match:
            return int(match.group(1))
        return None

    def _is_empty_page(self, html: str) -> bool:
        if len(html) < 300:
            return True
        return "products-flex-item" not in html

    def _get_html(self, client: httpx.Client, url: str) -> str | None:
        try:
            resp = client.get(url)
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

        # Product description
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

        # Price — from joined price-section text (handles split nodes like "823" + "р" + "/кг")
        price_rub = 0.0

        price_section_texts = []
        for el in item.xpath('.//*[contains(@class, "price")]//text()'):
            t = el.strip()
            if t:
                price_section_texts.append(t)
        price_joined = " ".join(price_section_texts)

        # Price per kg
        ppk_match = re.search(r"(\d[\d\s]*)\s*(?:р|руб)\s*/\s*(?:кг|kg)", price_joined, re.IGNORECASE)
        if not ppk_match:
            ppk_match = re.search(r"(\d[\d\s]*)\s*(?:р|руб)\s*/\s*(?:кг|kg)", " ".join(all_texts), re.IGNORECASE)

        if ppk_match:
            ppk = float(ppk_match.group(1).replace(" ", ""))
            if weight_g and weight_g > 0:
                price_rub = round(ppk * weight_g / 1000, 2)

        # Total price from price section
        if price_rub == 0.0:
            total_match = re.search(r"(\d[\d\s]*)\s*(?:р|руб)\b", price_joined, re.IGNORECASE)
            if total_match:
                p = safe_int_price(total_match.group(1))
                if p and 20 < p < 500000:
                    price_rub = float(p)

        # Total price from all text
        if price_rub == 0.0:
            for t in all_texts:
                if re.search(r'\d+\s*(?:г|гр|грамм|кг)\b', t, re.IGNORECASE):
                    continue
                if 'р/кг' in t or '/кг' in t:
                    continue
                p = safe_int_price(t)
                if p and 20 < p < 500000:
                    price_rub = float(p)
                    break

        if price_rub <= 0:
            return None

        # Product ID
        product_id = ""
        pid_el = item.xpath('.//a[@data-product-id]')
        if pid_el:
            product_id = pid_el[0].get("data-product-id", "")

        # URL
        url = ""
        link_el = item.xpath('.//a[contains(@class, "name")]/@href')
        if link_el:
            url = urljoin(self.base_url, link_el[0])

        # Brand + stock status
        brand = ""
        in_stock = True
        buy_el = item.xpath('.//a[contains(@class, "add2cart")]/@onclick')
        if buy_el:
            parts = buy_el[0].split(",")
            if len(parts) >= 4:
                brand = parts[3].strip().strip("'\" ")
        else:
            # No "buy" button = likely out of stock
            in_stock = False

        return RawProduct(
            site=self.name,
            product_id=product_id,
            name=name,
            brand=brand,
            weight_g=weight_g,
            price_rub=price_rub,
            in_stock=in_stock,
            url=url,
            raw={"article": article, "all_texts": all_texts},
        )
