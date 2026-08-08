"""One-shot: capture lavka products and save."""
from playwright.sync_api import sync_playwright
import json, re, time

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    page = browser.new_page()
    page.set_viewport_size({"width": 1400, "height": 900})

    all_category_data = []
    def handle(response):
        if response.status == 200 and "v2/category" in response.url and "lavka" in response.url:
            try:
                data = response.json()
                s = json.dumps(data)
                all_category_data.append({"size": len(s), "data": data})
                print(f"Captured category: {len(s)} bytes")
            except: pass

    page.on("response", handle)

    # Load page
    page.goto("https://lavka.yandex.ru/", timeout=45000, wait_until="commit")
    time.sleep(5)

    # Handle address dialog: just close it
    try:
        page.keyboard.press("Escape")
        time.sleep(2)
    except: pass

    # Click several top categories
    targets = ["Овощи, грибы и зелень", "Молочные продукты, сыр, яйца", "Мясо, птица, колбасы"]
    for target in targets:
        try:
            page.evaluate(f"""
                () => {{
                    const els = [...document.querySelectorAll('*')];
                    const el = els.find(e => e.innerText?.trim() === '{target}');
                    if (el) {{ el.click(); return 'clicked'; }}
                    // Try partial match
                    const el2 = els.find(e => e.innerText?.includes('{target[:10]}') && e.innerText?.length < 60);
                    if (el2) {{ el2.click(); return 'partial'; }}
                    return 'not found';
                }}
            """)
            time.sleep(3)
        except Exception as e:
            print(f"Click {target[:20]}: {e}")

    # Wait for API to settle
    time.sleep(5)

    print(f"Total responses: {len(all_category_data)}")

    # Save largest
    if all_category_data:
        largest = max(all_category_data, key=lambda x: x["size"])
        data = largest["data"]
        with open("data/lavka_full.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        # Extract products
        def extract(node, out):
            if isinstance(node, dict):
                if "id" in node and "title" in node and node.get("pricing"):
                    out.append(node)
                for v in node.values():
                    extract(v, out)
            elif isinstance(node, list):
                for item in node:
                    extract(item, out)

        products = []
        extract(data, products)
        print(f"Products found: {len(products)}")
        if products:
            print(f"Sample: {products[0].get('title', '')} — {products[0].get('pricing', {})}")

    browser.close()
