"""ProductTracker — управление персистентной CSV-таблицей продуктов.

Это ядро v2: накопительная таблица products.csv, ручное курирование
сопоставимости, обновление цен при повторных запусках.

Workflow:
  Проход 1 — scrape all sites, match by URL, update/add products
  Проход 2 — re-check missing comparable products via direct URL visit
  Проход 3 — save CSV + backup, generate report
"""

import csv
import datetime
import shutil
import re
from pathlib import Path
from typing import Optional

import httpx
from lxml import etree

from .config import DATA_DIR, OUR_TARGET_WEIGHT_G
from .normalize import extract_weight_grams, safe_int_price, is_red_caviar


# ---------------------------------------------------------------------------
# Column names in products.csv
# ---------------------------------------------------------------------------
COLUMNS = [
    "site",
    "product_name",
    "brand",
    "weight_g",
    "original_price_rub",
    "comparable_price_rub",
    "in_stock",
    "check_status",
    "is_comparable",
    "url",
    "first_seen",
    "last_checked",
]

CSV_PATH: Path = DATA_DIR / "products.csv"


# ---------------------------------------------------------------------------
# ProductTracker
# ---------------------------------------------------------------------------
class ProductTracker:
    """Manages the persistent product CSV table."""

    def __init__(self, target_weight_g: float = 120.0):
        self.target_weight_g = target_weight_g
        self.rows: list[dict] = []          # текущие данные
        self._url_index: dict[str, int] = {}  # url → индекс в self.rows
        self.today = datetime.date.today().isoformat()

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------
    def load(self) -> bool:
        """Load existing products.csv. Returns True if file existed."""
        if not CSV_PATH.exists():
            return False

        with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            self.rows = list(reader)

        # Строим индекс url → строка
        self._rebuild_index()
        return True

    def save(self) -> None:
        """Save to products.csv + dated backup."""
        # Backup current file if exists
        if CSV_PATH.exists():
            backup = DATA_DIR / f"products_{self.today}.csv"
            shutil.copy2(CSV_PATH, backup)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in self.rows:
                writer.writerow({k: row.get(k, "") for k in COLUMNS})

    def _rebuild_index(self) -> None:
        """Rebuild url → row_index map."""
        self._url_index.clear()
        for i, row in enumerate(self.rows):
            url = row.get("url", "").strip()
            if url:
                self._url_index[url] = i

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _new_row(
        site: str,
        name: str,
        brand: str,
        weight_g: Optional[float],
        price_rub: float,
        url: str,
        today: str,
        target_weight_g: float,
    ) -> dict:
        """Create a new row dict."""
        weight_g = weight_g or 0.0
        comparable = round(price_rub / weight_g * target_weight_g, 2) if weight_g > 0 else 0.0
        return {
            "site": site,
            "product_name": name,
            "brand": brand,
            "weight_g": str(weight_g) if weight_g else "",
            "original_price_rub": str(price_rub),
            "comparable_price_rub": str(comparable) if comparable else "",
            "in_stock": "да",
            "check_status": "да",
            "is_comparable": "",          # пользователь ещё не оценил
            "url": url,
            "first_seen": today,
            "last_checked": today,
        }

    def _update_row(self, row: dict, weight_g: Optional[float],
                    price_rub: float, in_stock: str = "да") -> None:
        """Update an existing row with fresh data."""
        weight_g = weight_g or 0.0
        row["weight_g"] = str(weight_g) if weight_g else ""
        row["original_price_rub"] = str(price_rub)
        comparable = round(price_rub / weight_g * self.target_weight_g, 2) if weight_g > 0 else 0.0
        row["comparable_price_rub"] = str(comparable) if comparable else ""
        row["in_stock"] = in_stock
        row["check_status"] = "да"
        row["last_checked"] = self.today

    # ------------------------------------------------------------------
    # Проход 1: scrape → match → update / add
    # ------------------------------------------------------------------
    def merge_scraped(self, scraped_products: list[dict]) -> dict:
        """Main pass: match scraped products against existing table.

        Each scraped product is dict with:
          site, product_id, name, brand, weight_g, price_rub, url, in_stock

        Returns stats dict.
        """
        stats = {"updated": 0, "added": 0, "skipped_non_comparable": 0}

        for sp in scraped_products:
            url = (sp.get("url") or "").strip()
            name = sp.get("name", "")
            if not name:
                continue

            # Фильтр: только красная икра
            if not is_red_caviar(name):
                continue

            price_rub = float(sp.get("price_rub", 0))
            if price_rub <= 0:
                continue

            weight_g = sp.get("weight_g")

            # Попытка найти по URL
            if url and url in self._url_index:
                idx = self._url_index[url]
                existing = self.rows[idx]

                # Пропускаем не-сопоставимые
                if existing.get("is_comparable", "") == "нет":
                    stats["skipped_non_comparable"] += 1
                    continue

                # Обновляем только если сопоставимый (да или пусто)
                self._update_row(existing, weight_g, price_rub)
                stats["updated"] += 1
            else:
                # Новый продукт
                brand = sp.get("brand", "")
                new_row = self._new_row(
                    site=sp.get("site", ""),
                    name=name,
                    brand=brand,
                    weight_g=weight_g,
                    price_rub=price_rub,
                    url=url,
                    today=self.today,
                    target_weight_g=self.target_weight_g,
                )
                self.rows.append(new_row)
                if url:
                    self._url_index[url] = len(self.rows) - 1
                stats["added"] += 1

        return stats

    # ------------------------------------------------------------------
    # Проход 2: re-check missing comparable products
    # ------------------------------------------------------------------
    def recheck_missing(self) -> dict:
        """Visit URLs of comparable products not found in current scrape.

        Returns stats dict.
        """
        stats = {"rechecked": 0, "now_unavailable": 0, "still_available": 0}

        client = httpx.Client(
            timeout=15.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
            follow_redirects=True,
        )

        for i, row in enumerate(self.rows):
            # Только сопоставимые продукты, которые НЕ были проверены сегодня
            if row.get("is_comparable", "") != "да":
                continue
            if row.get("check_status") == "да":
                continue  # уже проверили в проходе 1

            url = row.get("url", "").strip()
            if not url:
                # Без URL не можем перепроверить — помечаем как непроверенный
                row["check_status"] = "нет"
                continue

            # Пытаемся достать страницу продукта
            price = self._fetch_price_from_url(client, url, row.get("site", ""))

            if price is not None and price > 0:
                # Продукт жив — обновляем цену
                weight_g = float(row.get("weight_g", 0) or 0)
                self._update_row(row, weight_g, price, in_stock="да")
                stats["still_available"] += 1
            else:
                # Продукт недоступен
                row["in_stock"] = "нет"
                row["check_status"] = "да"
                row["last_checked"] = self.today
                stats["now_unavailable"] += 1

            stats["rechecked"] += 1

        return stats

    def _fetch_price_from_url(self, client: httpx.Client,
                              url: str, site: str) -> Optional[float]:
        """Try to extract current price from a product page URL."""
        try:
            resp = client.get(url)
            if resp.status_code >= 400:
                return None

            html = resp.text

            if "apeti.ru" in site:
                return self._extract_price_apeti(html)
            elif "seafood-shop" in site:
                return self._extract_price_seafood(html)
            elif "delikateska" in site:
                return self._extract_price_delikateska(html)

            # Generic: look for price patterns
            prices = re.findall(r'(\d[\d\s]*)\s*(?:₽|руб|р\.)', html)
            for p_str in prices:
                p = safe_int_price(p_str)
                if p and 200 < p < 500000:
                    return float(p)

        except Exception:
            pass

        return None

    def _extract_price_apeti(self, html: str) -> Optional[float]:
        """Extract price from apeti.ru product page."""
        try:
            tree = etree.HTML(html)
            for text in tree.xpath('//*[contains(@class, "product-price-block")]//text()'):
                p = safe_int_price(text)
                if p and p > 0:
                    return float(p)
        except Exception:
            pass
        return None

    def _extract_price_seafood(self, html: str) -> Optional[float]:
        """Extract price from seafood-shop.ru product page."""
        # Try embedded JSON first
        for match in re.finditer(r'"priceSource":(\d+)', html):
            return float(match.group(1))
        # Fallback
        for match in re.finditer(r'(\d[\d\s]*)\s*(?:₽|руб)', html):
            p = safe_int_price(match.group(1))
            if p and p > 100:
                return float(p)
        return None

    def _extract_price_delikateska(self, html: str) -> Optional[float]:
        """Extract price from delikateska.ru product page."""
        for match in re.finditer(r'(\d[\d\s]*)\s*(?:₽|руб)', html):
            p = safe_int_price(match.group(1))
            if p and p > 100:
                return float(p)
        return None

    # ------------------------------------------------------------------
    # Проход 3: report
    # ------------------------------------------------------------------
    def get_comparable_products(self) -> list[dict]:
        """Return rows with is_comparable='да', sorted by comparable_price."""
        comparable = [
            r for r in self.rows
            if r.get("is_comparable", "") == "да"
            and r.get("in_stock") == "да"
        ]
        # Sort by comparable_price ascending
        comparable.sort(
            key=lambda r: float(r.get("comparable_price_rub", 0) or 999999)
        )
        return comparable

    def get_new_products(self) -> list[dict]:
        """Return rows where is_comparable is empty (not yet evaluated)."""
        return [r for r in self.rows if r.get("is_comparable", "") == ""]

    def get_non_comparable(self) -> list[dict]:
        """Return rows with is_comparable='нет'."""
        return [r for r in self.rows if r.get("is_comparable", "") == "нет"]

    def get_summary(self) -> dict:
        """Return summary counts."""
        total = len(self.rows)
        comparable = sum(1 for r in self.rows if r.get("is_comparable") == "да")
        non_comparable = sum(1 for r in self.rows if r.get("is_comparable") == "нет")
        new = sum(1 for r in self.rows if r.get("is_comparable", "") == "")
        in_stock = sum(1 for r in self.rows if r.get("in_stock") == "да")

        comparable_in_stock = [
            r for r in self.rows
            if r.get("is_comparable") == "да" and r.get("in_stock") == "да"
        ]

        prices = [float(r["comparable_price_rub"]) for r in comparable_in_stock
                  if r.get("comparable_price_rub")]

        return {
            "total": total,
            "comparable": comparable,
            "non_comparable": non_comparable,
            "new_unevaluated": new,
            "in_stock": in_stock,
            "comparable_in_stock": len(comparable_in_stock),
            "min_price": min(prices) if prices else 0,
            "max_price": max(prices) if prices else 0,
            "avg_price": round(sum(prices) / len(prices), 2) if prices else 0,
        }


# ---------------------------------------------------------------------------
# Top-level helpers for cli.py
# ---------------------------------------------------------------------------
def run_tracker(scraped_all: list[dict],
                target_weight_g: float = 120.0) -> ProductTracker:
    """Run the full 3-pass tracking pipeline.

    Args:
        scraped_all: list of all RawProduct.to_dict() from all sites.
        target_weight_g: our product weight for comparable price calculation.

    Returns:
        ProductTracker with updated data (already saved to CSV).
    """
    tracker = ProductTracker(target_weight_g=target_weight_g)

    # Загружаем существующую таблицу
    existed = tracker.load()
    if existed:
        print(f"📂 Загружено {len(tracker.rows)} продуктов из products.csv\n")
    else:
        print("📂 products.csv не найден — будет создана новая таблица\n")

    # --- Проход 1: scrape → match ---
    print("🔄 Проход 1: сбор и сопоставление...")
    stats1 = tracker.merge_scraped(scraped_all)
    print(f"   Обновлено: {stats1['updated']}")
    print(f"   Добавлено: {stats1['added']}")
    print(f"   Пропущено (не сопоставимы): {stats1['skipped_non_comparable']}")

    # --- Проход 2: re-check missing ---
    print("🔄 Проход 2: проверка отсутствующих...")
    stats2 = tracker.recheck_missing()
    print(f"   Перепроверено URL: {stats2['rechecked']}")
    print(f"   Всё ещё доступны: {stats2['still_available']}")
    print(f"   Больше не в наличии: {stats2['now_unavailable']}")

    # --- Проход 3: save ---
    tracker.save()
    print(f"\n✅ Сохранено: {len(tracker.rows)} продуктов в data/products.csv")

    return tracker


def print_tracker_report(tracker: ProductTracker,
                         our_price: Optional[float] = None) -> None:
    """Print a formatted report from the tracker state."""
    summary = tracker.get_summary()
    comparable = tracker.get_comparable_products()
    new_products = tracker.get_new_products()

    print(f"\n{'='*70}")
    print(f"📊 ОТЧЁТ — {tracker.today}")
    print(f"{'='*70}")

    print(f"\n📋 Всего продуктов в таблице: {summary['total']}")
    print(f"   Сопоставимых (да): {summary['comparable']}")
    print(f"   Несопоставимых (нет): {summary['non_comparable']}")
    print(f"   Новых (не оценено): {summary['new_unevaluated']}")
    print(f"   В наличии: {summary['in_stock']}")

    # --- Таблица сопоставимых ---
    if comparable:
        our_w = tracker.target_weight_g
        header_price = f"За {our_w:.0f}г"
        print(f"\n┌{'─'*18}┬{'─'*32}┬{'─'*8}┬{'─'*10}┬{'─'*14}┐")
        print(f"│ {'Сайт':<16} │ {'Продукт':<30} │ {'Вес':>6} │ {'Цена':>8} │ {header_price:>12} │")
        print(f"├{'─'*18}┼{'─'*32}┼{'─'*8}┼{'─'*10}┼{'─'*14}┤")

        for r in comparable[:30]:
            site = r['site'][:16]
            name = r['product_name'][:30]
            w = r.get('weight_g', '?')
            price = f"{float(r.get('original_price_rub', 0)):,.0f}".replace(',', ' ')
            comp = f"{float(r.get('comparable_price_rub', 0)):,.0f}".replace(',', ' ')
            print(f"│ {site:<16} │ {name:<30} │ {w:>6} │ {price:>8} │ {comp:>12} │")

        print(f"└{'─'*18}┴{'─'*32}┴{'─'*8}┴{'─'*10}┴{'─'*14}┘")

        # Статистика по сопоставимым
        print(f"\n📈 Статистика (цена за {our_w:.0f} г, только сопоставимые в наличии):")
        print(f"   Минимум:  {summary['min_price']:,.0f} ₽".replace(',', ' '))
        print(f"   Средняя:  {summary['avg_price']:,.0f} ₽".replace(',', ' '))
        print(f"   Максимум: {summary['max_price']:,.0f} ₽".replace(',', ' '))

        if our_price:
            our_comparable = our_price  # already for our weight
            vs_avg = ((our_comparable - summary['avg_price']) / summary['avg_price'] * 100) \
                     if summary['avg_price'] > 0 else 0
            direction = "выше" if vs_avg > 0 else "ниже"
            print(f"\n💡 Наша цена: {our_price:,.0f} ₽ — на {abs(vs_avg):.0f}% {direction} рынка".replace(',', ' '))

    # --- Новые продукты ---
    if new_products:
        print(f"\n🆕 Новые продукты (нужно оценить сопоставимость): {len(new_products)}")
        print(f"   Открой data/products.csv и поставь 'да' или 'нет' в колонке is_comparable")
        for r in new_products[:5]:
            print(f"   - [{r['site']}] {r['product_name'][:50]} — {r.get('original_price_rub', '?')} ₽")
        if len(new_products) > 5:
            print(f"   ... и ещё {len(new_products) - 5}")
