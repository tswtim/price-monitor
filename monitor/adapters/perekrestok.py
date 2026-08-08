"""perekrestok.ru adapter — Playwright with stealth mode."""
import re, time
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams

class PerekrestokAdapter(BaseAdapter):
    def fetch(self) -> list[RawProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []
        all_products, seen_ids = [], set()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                args=["--no-sandbox","--disable-blink-features=AutomationControlled"]
            )
            page = browser.new_page()
            page.set_viewport_size({"width": 1400, "height": 900})
            # Hide automation
            page.evaluate("() => { Object.defineProperty(navigator, 'webdriver', { get: () => false }); }")

            try:
                page.goto("https://www.perekrestok.ru/", timeout=60000, wait_until="commit")
                page.wait_for_timeout(8000)
            except Exception as e:
                print(f"   [!] Page load failed: {e}")
                browser.close()
                return []

            html = page.content()
            if "капч" in html.lower() or "captcha" in html.lower() or "проверк" in html.lower():
                print("   [!] CAPTCHA detected — perekrestok.ru недоступен")
                browser.close()
                return []

            # Get category links
            categories = page.evaluate("""() => {
                const links = [...document.querySelectorAll('a[href*="/cat/"], a[href*="/catalog/"]')];
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
                    name = item["title"][:150]
                    all_products.append(RawProduct(site=self.name,product_id=pid,name=name,weight_g=extract_weight_grams(name),price_rub=price_rub,in_stock=True,url=item.get("href",""),raw={}))
                if (i+1) % 5 == 0:
                    print(f"   {i+1}/{len(categories)}, {len(all_products)} товаров")
            browser.close()
        return all_products
