#!/usr/bin/env python3
"""Competitor price monitoring — CLI entry point.

Usage:
    python -m monitor.cli --run          # Full scrape + report
    python -m monitor.cli --report       # Show last saved report
    python -m monitor.cli --history      # Show price trends
    python -m monitor.cli --selftest     # Run offline with saved fixtures
    python -m monitor.cli --quiet        # Scrape only, no output (for scheduler)
"""

import sys
import argparse
import json
import datetime
from pathlib import Path
from typing import Optional

# Fix Windows encoding for emoji/unicode output
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from .config import (
    DEFAULT_SITES,
    DEFAULT_TARGETS,
    SNAPSHOTS_DIR,
    ProductTarget,
    SiteConfig,
    load_user_price,
)
from .normalize import normalize_batch
from .store import start_run, finish_run, save_prices, get_last_run_prices, get_price_trends
from .report import terminal_report, save_csv, save_json
from .adapters.apeti import ApetiAdapter
from .adapters.seafood_shop import SeafoodShopAdapter
from .adapters.delikateska import DelikateskaAdapter


# Adapter registry
ADAPTERS = {
    "apeti.ru": ApetiAdapter,
    "seafood-shop.ru": SeafoodShopAdapter,
    "delikateska.ru": DelikateskaAdapter,
}


def run_scrape(sites: list[SiteConfig], target: ProductTarget,
               quiet: bool = False) -> list[dict]:
    """Scrape all sites and return normalized products."""
    all_products = []

    for site_config in sites:
        adapter_cls = ADAPTERS.get(site_config.name)
        if not adapter_cls:
            if not quiet:
                print(f"  [!] Нет адаптера для {site_config.name}")
            continue

        if not quiet:
            print(f"\n🔍 {site_config.display_name} ({site_config.name})...")

        # Start run record
        run_id = start_run(site_config.name)

        try:
            adapter = adapter_cls(site_config)
            raw_products = adapter.fetch()

            if not quiet:
                print(f"   Найдено: {len(raw_products)} товаров (до фильтрации)")

            # Save raw snapshot
            snapshot_path = _save_snapshot(site_config.name, raw_products)

            # Normalize with deduplication
            target_dict = {
                "name": target.name,
                "weight_g": target.weight_g,
                "keywords": target.keywords,
            }

            # Deduplicate by product_id before normalizing
            seen_ids = set()
            unique_raw = []
            for p in raw_products:
                pid = p.product_id
                if pid and pid in seen_ids:
                    continue
                if pid:
                    seen_ids.add(pid)
                unique_raw.append(p)

            normalized = normalize_batch([p.to_dict() for p in unique_raw], target_dict)

            if not quiet:
                print(f"   После фильтрации: {len(normalized)} товаров (красная икра)")
                exact = sum(1 for p in normalized if p.get("match_tier") == "exact")
                close = sum(1 for p in normalized if p.get("match_tier") == "close")
                print(f"   Совпадений: exact={exact}, close={close}")

            # Save to DB
            save_prices(run_id, normalized)
            finish_run(run_id, products_found=len(normalized),
                       snapshot_path=str(snapshot_path) if snapshot_path else None)

            all_products.extend(normalized)

        except Exception as e:
            if not quiet:
                print(f"   ❌ Ошибка: {e}")
            finish_run(run_id, error=str(e))

    return all_products


def _save_snapshot(site_name: str, products) -> Optional[Path]:
    """Save raw products as a JSON snapshot."""
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = SNAPSHOTS_DIR / f"{site_name}_{now}.json"
        data = {
            "site": site_name,
            "collected_at": datetime.datetime.now().isoformat(),
            "count": len(products),
            "products": [p.to_dict() if hasattr(p, 'to_dict') else p for p in products],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        return None


def cmd_run(quiet: bool = False) -> None:
    """Run the full scrape pipeline."""
    target = DEFAULT_TARGETS[0]

    # Override with user's price if set
    user_price = load_user_price()
    if user_price:
        target.our_price_rub = user_price

    products = run_scrape(DEFAULT_SITES, target, quiet=quiet)

    if not products:
        if not quiet:
            print("\n❌ Не удалось собрать данные ни с одного сайта.")
        return

    # Sort by price_per_100g
    products.sort(key=lambda p: p.get("price_per_100g") or float("inf"))

    # Report
    if not quiet:
        previous = get_last_run_prices()
        report = terminal_report(products, target, previous)
        print(report)

    # Save CSV and JSON
    csv_path = save_csv(products)
    json_path = save_json(products)

    if not quiet:
        print(f"\n📁 Отчёт сохранён:")
        print(f"   CSV: {csv_path}")
        print(f"   JSON: {json_path}")


def cmd_report() -> None:
    """Show the most recent report."""
    products = get_last_run_prices()
    if not products:
        print("❌ Нет сохранённых данных. Запустите --run сначала.")
        return

    target = DEFAULT_TARGETS[0]
    user_price = load_user_price()
    if user_price:
        target.our_price_rub = user_price

    print(terminal_report(products, target))


def cmd_history() -> None:
    """Show price trend history."""
    products = get_last_run_prices()
    if not products:
        print("❌ Нет сохранённых данных.")
        return

    # Get unique product keys
    keys = list(set(p.get("product_key", "") for p in products if p.get("product_key")))
    trends = get_price_trends(keys[:20], limit=5)  # Show top 20 products

    print("\n📊 История цен\n")

    for key, entries in trends.items():
        if len(entries) < 2:
            continue
        latest = entries[0]
        name = latest.get("name", key)[:50]
        site = latest.get("site", "")
        prices = [e.get("price_rub", 0) for e in entries]
        dates = [e.get("collected_at", "")[:10] for e in entries]

        trend_str = " → ".join(f"{p:,.0f} ₽".replace(",", " ") for p in reversed(prices))
        print(f"  {site}: {name}")
        print(f"     {trend_str}")
        print()


def cmd_selftest() -> None:
    """Run adapters against saved fixtures."""
    fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    if not fixtures_dir.exists() or not list(fixtures_dir.glob("*.json")):
        print("⚠ Нет сохранённых фикстур. Сначала сделайте --run для создания снапшотов.")
        print("  Фикстуры можно создать из снапшотов в data/snapshots/")
        return

    print(f"🧪 Запуск тестов на фикстурах из {fixtures_dir}\n")

    for fixture_path in sorted(fixtures_dir.glob("*.json")):
        try:
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            site = data.get("site", fixture_path.stem)
            products_count = len(data.get("products", []))
            print(f"  ✅ {site}: {products_count} товаров в фикстуре")
        except Exception as e:
            print(f"  ❌ {fixture_path.name}: ошибка — {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Мониторинг цен конкурентов на красную икру",
    )
    parser.add_argument("--run", action="store_true",
                        help="Запустить сбор цен со всех сайтов")
    parser.add_argument("--report", action="store_true",
                        help="Показать последний сохранённый отчёт")
    parser.add_argument("--history", action="store_true",
                        help="Показать историю изменения цен")
    parser.add_argument("--selftest", action="store_true",
                        help="Запустить тесты на сохранённых фикстурах")
    parser.add_argument("--quiet", action="store_true",
                        help="Тихий режим (только запись в БД, без вывода)")

    args = parser.parse_args()

    if args.run:
        cmd_run(quiet=args.quiet)
    elif args.report:
        cmd_report()
    elif args.history:
        cmd_history()
    elif args.selftest:
        cmd_selftest()
    else:
        # Default: show report if data exists, else suggest --run
        products = get_last_run_prices()
        if products:
            cmd_report()
        else:
            parser.print_help()
            print("\n💡 Нет сохранённых данных. Запустите: python -m monitor.cli --run")


if __name__ == "__main__":
    main()
