"""Test Chrome profile approach for blocked sites."""
from playwright.sync_api import sync_playwright
import os

user_data = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")

with sync_playwright() as pw:
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=user_data,
            channel="chrome",
            headless=False,
        )
        page = context.new_page()
        print(f"Using Chrome profile")

        for site, name in [
            ("https://samokat.ru/", "Samokat"),
            ("https://www.perekrestok.ru/", "Perekrestok"),
            ("https://www.ozon.ru/category/supermarket-25000/", "Ozon"),
        ]:
            try:
                page.goto(site, timeout=30000, wait_until="commit")
                page.wait_for_timeout(5000)
                text = page.evaluate("() => document.body?.innerText?.substring(0, 300)")
                blocked = any(w in text.lower() for w in ["проверк", "капч", "captcha", "запрещен", "соединени"])
                cats = 0 if blocked else page.evaluate("() => [...document.querySelectorAll('a[href*=\"/category/\"], a[href*=\"/catalog/\"]')].length")
                print(f"{name}: {'BLOCKED' if blocked else f'OK ({cats} categories)'}")
            except Exception as e:
                print(f"{name}: ERROR {e}")

        context.close()
    except Exception as e:
        print(f"Profile error: {e}. Make sure Chrome is CLOSED before running.")
