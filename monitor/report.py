"""Formatted output: terminal tables, CSV, JSON, Markdown."""

import csv
import json
import datetime
from pathlib import Path
from typing import Optional

from .config import REPORTS_DIR, USER_PRICE_FILE, ProductTarget
from .store import get_last_run_prices


# --- Terminal table ---

def terminal_report(
    products: list[dict],
    target: Optional[ProductTarget] = None,
    previous: Optional[list[dict]] = None,
) -> str:
    """Generate a formatted terminal table of prices.

    Products should be already normalized, sorted by price_per_100g.
    """
    if not products:
        return "❌ Нет данных для отображения."

    lines = []
    target_name = target.name if target else "Икра горбуши"
    target_weight = target.weight_g if target else 120
    our_price = target.our_price_rub if target else None

    # Header
    lines.append(f"\n🏷️  {target_name} ({target_weight:.0f} г) — сравнение цен конкурентов\n")

    # Compute stats
    exact = [p for p in products if p.get("match_tier") == "exact"]
    close = [p for p in products if p.get("match_tier") in ("exact", "close")]
    all_products = close if close else products

    prices_per_100g = [p["price_per_100g"] for p in all_products if p.get("price_per_100g")]
    if prices_per_100g:
        min_price = min(prices_per_100g)
        max_price = max(prices_per_100g)
        avg_price = sum(prices_per_100g) / len(prices_per_100g)
    else:
        min_price = max_price = avg_price = 0

    # Build table
    # Width calculations
    site_w = 18
    name_w = 35
    brand_w = 15
    weight_w = 8
    price_w = 10
    unit_w = 14

    sep = f"├{'─'*site_w}┼{'─'*name_w}┼{'─'*brand_w}┼{'─'*weight_w}┼{'─'*price_w}┼{'─'*unit_w}┤"
    top = f"┌{'─'*site_w}┬{'─'*name_w}┬{'─'*brand_w}┬{'─'*weight_w}┬{'─'*price_w}┬{'─'*unit_w}┐"
    bot = f"└{'─'*site_w}┴{'─'*name_w}┴{'─'*brand_w}┴{'─'*weight_w}┴{'─'*price_w}┴{'─'*unit_w}┘"

    lines.append(top)
    lines.append(
        f"│ {'Сайт':<{site_w-2}} │ {'Продукт':<{name_w-2}} │ "
        f"{'Бренд':<{brand_w-2}} │ {'Вес':<{weight_w-2}} │ "
        f"{'Цена':<{price_w-2}} │ {'Цена/100г':<{unit_w-2}} │"
    )
    lines.append(sep)

    # Sort by price_per_100g ascending
    sorted_products = sorted(
        all_products,
        key=lambda p: p.get("price_per_100g") or float("inf"),
    )

    for p in sorted_products:
        site = _truncate(p.get("site", ""), site_w - 2)
        name = _truncate(p.get("name", ""), name_w - 2)
        brand = _truncate(p.get("brand", ""), brand_w - 2)
        weight = f"{p.get('weight_g', '?')} г"
        weight = weight[:weight_w - 2]
        price = f"{p.get('price_rub', 0):,.0f} ₽".replace(",", " ")
        price = price[:price_w - 2]
        unit = f"{p.get('price_per_100g', 0):,.0f} ₽".replace(",", " ") if p.get("price_per_100g") else "—"
        unit = unit[:unit_w - 2]

        lines.append(
            f"│ {site:<{site_w-2}} │ {name:<{name_w-2}} │ "
            f"{brand:<{brand_w-2}} │ {weight:<{weight_w-2}} │ "
            f"{price:<{price_w-2}} │ {unit:<{unit_w-2}} │"
        )

    # Our price row
    if our_price and target_weight:
        our_unit = our_price / target_weight * 100
        lines.append(sep)
        lines.append(
            f"│ {'🏠 НАША ЦЕНА':<{site_w-2}} │ {target_name[:name_w-2]:<{name_w-2}} │ "
            f"{'—':<{brand_w-2}} │ {f'{target_weight:.0f} г':<{weight_w-2}} │ "
            f"{f'{our_price:,.0f} ₽'.replace(',', ' '):<{price_w-2}} │ "
            f"{f'{our_unit:,.0f} ₽'.replace(',', ' '):<{unit_w-2}} │"
        )

    lines.append(bot)

    # Stats
    lines.append("")
    lines.append(f"📊 Статистика рынка (по цене за 100 г):")
    lines.append(f"   Минимум:  {min_price:,.0f} ₽".replace(",", " "))
    lines.append(f"   Средняя:  {avg_price:,.0f} ₽".replace(",", " "))
    lines.append(f"   Максимум: {max_price:,.0f} ₽".replace(",", " "))

    if our_price and target_weight:
        our_unit = our_price / target_weight * 100
        vs_avg = ((our_unit - avg_price) / avg_price * 100) if avg_price > 0 else 0
        direction = "выше" if vs_avg > 0 else "ниже"
        lines.append(f"\n💡 Наша цена {our_unit:,.0f} ₽/100г — {abs(vs_avg):.0f}% {direction} рынка".replace(",", " "))

    if exact:
        cheapest_exact = min(exact, key=lambda p: p.get("price_per_100g", float("inf")))
        lines.append(f"🏆 Самый дешёвый аналог: {cheapest_exact['name'][:40]} — "
                     f"{cheapest_exact['price_per_100g']:,.0f} ₽/100г".replace(",", " "))

    # Deltas from previous run
    if previous:
        deltas = _compute_deltas(sorted_products, previous)
        if deltas:
            lines.append("\n📈 Изменения с прошлой проверки:")
            for d in deltas[:10]:
                lines.append(f"   {d}")

    return "\n".join(lines)


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string to max_len, adding ellipsis if needed."""
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"


def _compute_deltas(current: list[dict], previous: list[dict]) -> list[str]:
    """Compare current prices with previous run, return list of delta strings."""
    prev_map = {p.get("product_key", ""): p for p in previous}
    deltas = []

    for p in current:
        key = p.get("product_key", "")
        if key in prev_map:
            prev_price = prev_map[key].get("price_rub", 0)
            curr_price = p.get("price_rub", 0)
            if prev_price > 0 and curr_price > 0:
                delta = (curr_price - prev_price) / prev_price * 100
                if abs(delta) >= 5:
                    arrow = "⬆" if delta > 0 else "⬇"
                    deltas.append(
                        f"{arrow} {p['site']}: {p['name'][:30]} — "
                        f"{prev_price:,.0f} → {curr_price:,.0f} ₽ "
                        f"({delta:+.0f}%)".replace(",", " ")
                    )

    return deltas


# --- CSV export ---

def save_csv(products: list[dict], filepath: Optional[Path] = None) -> Path:
    """Save products to a CSV file."""
    if filepath is None:
        date_str = datetime.date.today().isoformat()
        filepath = REPORTS_DIR / f"prices_{date_str}.csv"

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "site", "product_id", "name", "brand", "weight_g",
            "price_rub", "price_per_100g", "price_per_kg",
            "in_stock", "url", "match_tier",
        ])
        writer.writeheader()
        for p in products:
            writer.writerow({
                k: p.get(k, "") for k in writer.fieldnames
            })

    return filepath


# --- JSON export ---

def save_json(products: list[dict], filepath: Optional[Path] = None) -> Path:
    """Save products to a JSON file."""
    if filepath is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        filepath = REPORTS_DIR / f"prices_{date_str}.json"

    data = {
        "collected_at": datetime.datetime.now().isoformat(),
        "product_count": len(products),
        "products": products,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return filepath
