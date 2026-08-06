#!/usr/bin/env python3
"""Competitor price monitoring v3 — CLI entry point.

Multi-SKU analysis with keyword matching, XLSX output.

Usage:
    python -m monitor.cli --run          # Full scrape + match + save XLSX
    python -m monitor.cli --status       # Quick summary
    python -m monitor.cli --quiet        # Scrape + save, no output
"""

import sys
import argparse
import json

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .config import (
    DEFAULT_SITES, DEFAULT_TARGETS, USER_PRICE_FILE,
    OUR_TARGET_WEIGHT_G, SKUS_PATH, OUTPUT_XLSX_PATH,
    SiteConfig, load_user_price,
)
from .matcher import load_skus, create_skus_template
from .tracker import run_tracker, print_tracker_report, ProductTracker
from .adapters.apeti import ApetiAdapter
from .adapters.seafood_shop import SeafoodShopAdapter
from .adapters.delikateska import DelikateskaAdapter

ADAPTERS = {
    "apeti.ru": ApetiAdapter,
    "seafood-shop.ru": SeafoodShopAdapter,
    "delikateska.ru": DelikateskaAdapter,
}


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


def cmd_run(quiet: bool = False) -> None:
    """Full run: scrape → match → save XLSX."""
    # ---- Load SKUs ----
    if not SKUS_PATH.exists():
        print("📝 Файл my_skus.csv не найден. Создаю шаблон...")
        create_skus_template()
        print(f"   ✅ Шаблон создан: {SKUS_PATH}")
        print(f"   📋 Заполни его своими SKU (название, наша цена, вес)")
        print(f"   📋 Затем запусти скрипт снова.")
        return

    skus = load_skus()
    if not skus:
        print("❌ my_skus.csv пуст или не содержит SKU.")
        return

    print(f"📋 Загружено {len(skus)} SKU из {SKUS_PATH}")

    # ---- Scrape ----
    if not quiet:
        print(f"\n{'='*60}")
        print("СБОР ДАННЫХ")
        print("=" * 60)

    scraped = collect_all_sites(DEFAULT_SITES, quiet=quiet)

    if not scraped:
        print("\n❌ Не удалось собрать данные ни с одного сайта.")
        return

    if not quiet:
        print(f"\n📦 Всего собрано: {len(scraped)} товаров (до фильтрации)")

    # ---- Tracker ----
    if not quiet:
        print(f"\n{'='*60}")
        print("СОПОСТАВЛЕНИЕ")
        print("=" * 60)

    tracker = run_tracker(scraped, skus)

    # ---- Report ----
    if not quiet:
        print(f"\n{'='*60}")
        print("ОТЧЁТ")
        print("=" * 60)
        print_tracker_report(tracker)


def cmd_status() -> None:
    """Quick status from existing XLSX."""
    skus = load_skus()
    if not skus:
        print("❌ my_skus.csv не найден.")
        print("   Запустите python -m monitor.cli --run для создания шаблона.")
        return

    tracker = ProductTracker(skus=skus)
    if not tracker.load():
        print("❌ result.xlsx не найден.")
        print("   Запустите: python -m monitor.cli --run")
        return

    print_tracker_report(tracker)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Мониторинг цен конкурентов v3 — мульти-SKU + XLSX",
    )
    parser.add_argument("--run", action="store_true",
                        help="Собрать цены, сопоставить с SKU, сохранить XLSX")
    parser.add_argument("--status", action="store_true",
                        help="Краткая сводка из существующего XLSX")
    parser.add_argument("--quiet", action="store_true",
                        help="Тихий режим (только запись XLSX)")
    parser.add_argument("--init-skus", action="store_true",
                        help="Создать шаблон my_skus.csv")

    args = parser.parse_args()

    if args.init_skus:
        create_skus_template()
        print(f"✅ Шаблон создан: {SKUS_PATH}")
    elif args.run:
        cmd_run(quiet=args.quiet)
    elif args.status:
        cmd_status()
    else:
        # Default: status if data exists, else help
        if SKUS_PATH.exists() and OUTPUT_XLSX_PATH.exists():
            cmd_status()
        else:
            parser.print_help()
            print(f"\n💡 Первый запуск:")
            print(f"   1. python -m monitor.cli --init-skus  (создать шаблон SKU)")
            print(f"   2. Заполни my_skus.csv своими товарами")
            print(f"   3. python -m monitor.cli --run        (запустить анализ)")


if __name__ == "__main__":
    main()
