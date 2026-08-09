"""samokat.ru adapter — Playwright with persistent profile."""
import re, os
from .base import BaseAdapter, RawProduct
from ..normalize import extract_weight_grams

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "browser_profile")

class SamokatAdapter(BaseAdapter):
    def fetch(self) -> list[RawProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []
        all_products, seen_ids = [], set()
        os.makedirs(PROFILE_DIR, exist_ok=True)
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR, headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1400, "height": 900}
            )
            page = context.new_page()
            try:
                page.goto("https://samokat.ru/", timeout=45000, wait_until="commit")
                page.wait_for_timeout(5000)
            except:
                context.close(); return []
            text = page.evaluate("() => document.body?.innerText?.substring(0, 300)") or ""
            if any(w in text.lower() for w in ["проверк","капч","captcha","запрещен"]):
                print("   [!] Капча — реши её в открывшемся окне браузера и запусти скрипт снова")
                print("   (профиль сохранится, второй раз капчи не будет)")
                page.wait_for_timeout(60000)
                context.close(); return []

            categories = page.evaluate("""() => {
                const links = [...document.querySelectorAll('a[href*="/category/"], a[href*="/catalog/"]')];
                const seen = new Set();
                return links.map(a => ({name: a.innerText?.trim(), href: a.href}))
                    .filter(c => c.name && c.name.length > 2 && !seen.has(c.href) && seen.add(c.href));
            }""")
            print(f"   Категорий: {len(categories)}")
            for i, cat in enumerate(categories):
                try: page.goto(cat["href"], timeout=20000, wait_until="commit"); page.wait_for_timeout(2000)
                except: continue
                for _ in range(15):
                    try: page.evaluate("window.scrollTo(0, document.body?.scrollHeight || 0)")
                    except: break
                    page.wait_for_timeout(500)
                items = page.evaluate("""() => {
                    const cards = [...document.querySelectorAll('[class*="product"], [class*="card"], [class*="item"]')];
                    return cards.map(c => {
                        const t = c.querySelector('[class*="title"], [class*="name"], h3, h4')?.innerText?.trim() || '';
                        const pr = c.innerText?.match(/(\\d[\\d\\s]*)\\s*[₽]/)?.[1]?.replace(/\\s/g, '');
                        return {title: t, price: pr, href: c.querySelector('a')?.href || ''};
                    }).filter(p => p.title && p.price);
                }""")
                for item in items:
                    pid = item.get("href","") or item.get("title","")
                    if pid in seen_ids: continue
                    seen_ids.add(pid)
                    try: price_rub = float(item["price"].replace(" ",""))
                    except: continue
                    if price_rub <= 0: continue
                    all_products.append(RawProduct(site=self.name,product_id=pid,name=item["title"],weight_g=extract_weight_grams(item["title"]),price_rub=price_rub,in_stock=True,url=item.get("href",""),raw={}))
                if (i+1) % 10 == 0: print(f"   {i+1}/{len(categories)}, {len(all_products)} товаров")
            context.close()
        return all_products
