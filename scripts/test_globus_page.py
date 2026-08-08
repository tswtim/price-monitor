"""Test globus pagination — click Показать еще via JS."""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    page = browser.new_page()

    page.goto("https://online.globus.ru/catalog/ryba-ikra-moreprodukty-11191953", timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    # Click via JS
    for i in range(25):
        clicked = page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button, span, div')];
            for (const btn of btns) {
                if (btn.innerText?.includes('Показать еще')) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }""")
        if not clicked:
            print(f"No button at click {i+1}")
            break
        page.wait_for_timeout(1000)

    # Count products
    result = page.evaluate("""() => {
        const el = document.getElementById('__NEXT_DATA__');
        if (!el) return 0;
        const state = JSON.parse(el.textContent);
        const queries = state?.props?.pageProps?.dehydratedState?.queries || [];
        for (const q of queries) {
            if (q?.queryKey?.[0] === 'catalog-product-list') {
                const pages = q?.state?.data?.pages || [];
                let n = 0;
                for (const p of pages) n += (p?.data?.products?.items || []).length;
                const total = pages[0]?.data?.products?.pagination?.total || 0;
                return {"pages": pages.length, "items": n, "total": total};
            }
        }
        return 0;
    }""")

    print(f"After {i+1} clicks: {result}")
    browser.close()
