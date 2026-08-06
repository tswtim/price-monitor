#!/usr/bin/env python3
"""Competitor price monitoring v2 — CLI entry point.

Usage:
    python -m monitor.cli --run          # Full scrape + update products.csv
    python -m monitor.cli --report       # Show report from products.csv
    python -m monitor.cli --quiet        # Scrape only, no output (for scheduler)
"""

import sys
import argparse
import json
import datetime

# Fix Windows encoding for emoji/unicode output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .config import (
    DEFAULT_SITES,
    DEFAULT_TARGETS,
    USER_PRICE_FILE,
    OUR_TARGET_WEIGHT_G,
    ProductTarget,
    SiteConfig,
    load_user_price,
)
from .tracker import run_tracker, print_tracker_report, ProductTracker
from .adapters.apeti import ApetiAdapter
from .adapters.seafood_shop import SeafoodShopAdapter
from .adapters.delikateska import DelikateskaAdapter


# Adapter registry
ADAPTERS = {
    "apeti.ru": ApetiAdapter,
    "seafood-shop.ru": SeafoodShopAdapter,
    "delikateska.ru": DelikateskaAdapter,
}


# ---------------------------------------------------------------------------
# Collect raw products from all sites
# ---------------------------------------------------------------------------
def collect_all_sites(sites: list[SiteConfig], quiet: bool = False) -> list[dict]:
    """Scrape all configured sites, return list of raw product dicts."""
    all_raw: list[dict] = []

    for site_config in sites:
        adapter_cls = ADAPTERS.get(site_config.name)
        if not adapter_cls:
            if not quiet:
                print(f"  [!] Нет адаптера для {site_config.name}")
            continue

        if not quiet:
            print(f"\n🔍 {site_config.display_name} ({site_config.name})...")

        try:
            adapter = adapter_cls(site_config)
            raw_products = adapter.fetch()

            if not quiet:
                print(f"   Собрано: {len(raw_products)} товаров")

            # Deduplicate by product_id
            seen_ids = set()
            for p in raw_products:
                pid = p.product_id
                if pid and pid in seen_ids:
                    continue
                if pid:
                    seen_ids.add(pid)
                all_raw.append(p.to_dict())

        except Exception as e:
            if not quiet:
                print(f"   [!] Ошибка: {e}")

    return all_raw


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_run(quiet: bool = False) -> None:
    """Full run: scrape → tracker → report."""
    target = DEFAULT_TARGETS[0]

    # User price
    our_price = load_user_price() or target.our_price_rub
    target_weight = target.weight_g or OUR_TARGET_WEIGHT_G

    # --- Scrape ---
    if not quiet:
        print("=" * 60)
        print("СБОР ДАННЫХ")
        print("=" * 60)

    scraped = collect_all_sites(DEFAULT_SITES, quiet=quiet)

    if not scraped:
        print("\n❌ Не удалось собрать данные ни с одного сайта.")
        return

    if not quiet:
        print(f"\n📦 Всего собрано (до фильтрации): {len(scraped)} товаров")

    # --- Tracker ---
    if not quiet:
        print(f"\n{'=' * 60}")
        print("ОБРАБОТКА")
        print("=" * 60)

    tracker = run_tracker(scraped, target_weight_g=target_weight)

    # --- Report ---
    if not quiet:
        print(f"\n{'=' * 60}")
        print("ОТЧЁТ")
        print("=" * 60)
        print_tracker_report(tracker, our_price=our_price)

    # Summary
    summary = tracker.get_summary()
    new_count = summary.get("new_unevaluated", 0)
    if new_count > 0:
        print(f"\n📝 Открой data/products.csv и поставь 'да' или 'нет'")
        print(f"   в колонке is_comparable для {new_count} новых продуктов.")


def cmd_report() -> None:
    """Show report from existing products.csv."""
    tracker = ProductTracker(target_weight_g=OUR_TARGET_WEIGHT_G)

    if not tracker.load():
        print("❌ data/products.csv не найден.")
        print("   Запустите: python -m monitor.cli --run")
        return

    target = DEFAULT_TARGETS[0]
    our_price = load_user_price() or target.our_price_rub

    print_tracker_report(tracker, our_price=our_price)

    new_count = tracker.get_summary().get("new_unevaluated", 0)
    if new_count > 0:
        print(f"\n📝 {new_count} новых продуктов ждут оценки в data/products.csv")


def cmd_status() -> None:
    """Quick status: just show summary counts."""
    tracker = ProductTracker()
    if not tracker.load():
        print("Нет данных. Запустите: python -m monitor.cli --run")
        return

    s = tracker.get_summary()
    print(f"Всего продуктов: {s['total']}")
    print(f"  Сопоставимых:   {s['comparable']} (в наличии: {s['comparable_in_stock']})")
    print(f"  Несопоставимых: {s['non_comparable']}")
    print(f"  Новых:          {s['new_unevaluated']}")

    if s['comparable_in_stock'] > 0:
        print(f"\nЦены за {OUR_TARGET_WEIGHT_G:.0f} г:")
        print(f"  Мин: {s['min_price']:,.0f} ₽".replace(',', ' '))
        print(f"  Ср:  {s['avg_price']:,.0f} ₽".replace(',', ' '))
        print(f"  Макс:{s['max_price']:,.0f} ₽".replace(',', ' '))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Мониторинг цен конкурентов v2 — накопительная таблица продуктов",
    )
    parser.add_argument("--run", action="store_true",
                        help="Собрать цены и обновить products.csv")
    parser.add_argument("--report", action="store_true",
                        help="Показать отчёт из products.csv")
    parser.add_argument("--status", action="store_true",
                        help="Краткая сводка")
    parser.add_argument("--quiet", action="store_true",
                        help="Тихий режим (только запись CSV)")
    parser.add_argument("--target-weight", type=float, default=OUR_TARGET_WEIGHT_G,
                        help=f"Вес нашего продукта для сопоставления (по умолчанию: {OUR_TARGET_WEIGHT_G:.0f} г)")

    args = parser.parse_args()

    if args.run:
        cmd_run(quiet=args.quiet)
    elif args.report:
        cmd_report()
    elif args.status:
        cmd_status()
    else:
        # Default: show status if data exists
        tracker = ProductTracker()
        if tracker.load():
            cmd_status()
            print("\nПолный отчёт: python -m monitor.cli --report")
        else:
            parser.print_help()
            print("\n💡 Запустите: python -m monitor.cli --run")


if __name__ == "__main__":
    main()
