"""lavka.yandex.ru adapter — DOM scraping via Playwright."""
import re, time
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams

class LavkaAdapter(BaseAdapter):
    def fetch(self) -> list[RawProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []
        all_products, seen_ids = [], set()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto("https://lavka.yandex.ru/", timeout=45000, wait_until="commit")
            page.wait_for_timeout(5000)
            try: page.keyboard.press("Escape"); time.sleep(2)
            except: pass

            # Get category links from the catalog sidebar
            categories = page.evaluate("""() => {
                const links = [...document.querySelectorAll('a[href*="/category/"]')];
                const seen = new Set();
                return links.map(a => {return {name: a.innerText?.trim(), href: a.href}})
                    .filter(c => c.name && c.name.length > 2 && !seen.has(c.href) && seen.add(c.href));
            }""")
            print(f"   Категорий: {len(categories)}")

            for i, cat in enumerate(categories):
                try:
                    page.goto(cat["href"], timeout=20000, wait_until="commit")
                    page.wait_for_timeout(2000)
                except: continue

                # Scroll to load all products
                for _ in range(20):
                    try:
                        page.evaluate("window.scrollTo(0, document.body?.scrollHeight || 0)")
                        page.wait_for_timeout(500)
                    except:
                        break

                # Extract products from DOM
                items = page.evaluate("""() => {
                    const cards = [...document.querySelectorAll('[data-testid="product-card"], [class*="product"], [class*="card"]')];
                    return cards.map(c => {
                        const title = c.querySelector('[class*="title"], [class*="name"], h3, h4')?.innerText?.trim() || '';
                        const price = c.innerText?.match(/(\\d[\\d\\s]*)\\s*[₽]/)?.[1]?.replace(/\\s/g, '');
                        const weight = c.innerText?.match(/(\\d+[.,]?\\d*)\\s*(г|кг|мл|л|шт)/)?.[0];
                        const id = c.getAttribute('data-id') || c.getAttribute('id') || '';
                        const href = c.querySelector('a')?.href || '';
                        return {title, price, weight, id, href};
                    }).filter(p => p.title && p.price);
                }""")

                for item in items:
                    pid = item.get("id") or item.get("href","")
                    if pid and pid in seen_ids: continue
                    if pid: seen_ids.add(pid)
                    name = item.get("title","")
                    try: price_rub = float(item.get("price","0").replace(" ",""))
                    except: continue
                    if price_rub <= 0: continue
                    wg = None
                    wt = item.get("weight","")
                    if wt:
                        m = re.search(r'([\d.,]+)\s*(г|кг|мл|л)', wt)
                        if m:
                            v = float(m.group(1).replace(",","."))
                            wg = v*1000 if m.group(2) in ("кг","л") else v
                    if wg is None: wg = extract_weight_grams(name)
                    all_products.append(RawProduct(site=self.name,product_id=pid,name=name,weight_g=wg,price_rub=price_rub,in_stock=True,url=item.get("href",""),raw={}))

                if (i+1) % 10 == 0:
                    print(f"   {i+1}/{len(categories)}, {len(all_products)} товаров")

            browser.close()
        return all_products
