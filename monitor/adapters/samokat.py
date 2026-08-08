"""samokat.ru adapter — DOM scraping via Playwright."""
import re
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams

class SamokatAdapter(BaseAdapter):
    def fetch(self) -> list[RawProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []
        all_products, seen_ids = [], set()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto("https://samokat.ru/", timeout=45000, wait_until="commit")
            page.wait_for_timeout(5000)

            # Get category links
            categories = page.evaluate("""() => {
                const links = [...document.querySelectorAll('a[href*="/category/"], a[href*="/catalog/"]')];
                const seen = new Set();
                return links.map(a => ({name: a.innerText?.trim(), href: a.href}))
                    .filter(c => c.name && c.name.length > 2 && !seen.has(c.href) && seen.add(c.href));
            }""")
            print(f"   Категорий: {len(categories)}")

            for i, cat in enumerate(categories):
                try:
                    page.goto(cat["href"], timeout=20000, wait_until="commit")
                    page.wait_for_timeout(2000)
                except: continue
                for _ in range(15):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(500)
                items = page.evaluate("""() => {
                    const cards = [...document.querySelectorAll('[class*="product"], [class*="card"], [class*="item"]')];
                    return cards.map(c => {
                        const title = c.querySelector('[class*="title"], [class*="name"], h3, h4')?.innerText?.trim() || '';
                        const price = c.innerText?.match(/(\\d[\\d\\s]*)\\s*[₽]/)?.[1]?.replace(/\\s/g, '');
                        const id = c.getAttribute('data-id') || '';
                        const href = c.querySelector('a')?.href || '';
                        return {title, price, id, href};
                    }).filter(p => p.title && p.price);
                }""")
                for item in items:
                    pid = item.get("id") or item.get("href","")
                    if pid and pid in seen_ids: continue
                    if pid: seen_ids.add(pid)
                    try: price_rub = float(item["price"].replace(" ",""))
                    except: continue
                    if price_rub <= 0: continue
                    name = item["title"]
                    all_products.append(RawProduct(site=self.name,product_id=pid,name=name,weight_g=extract_weight_grams(name),price_rub=price_rub,in_stock=True,url=item.get("href",""),raw={}))
                if (i+1) % 10 == 0:
                    print(f"   {i+1}/{len(categories)}, {len(all_products)} товаров")
            browser.close()
        return all_products
