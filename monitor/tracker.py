"""ProductTracker v3 — multi-SKU matching with XLSX output.

Manages the persistent product table (data/result.xlsx).
Matches scraped products against user's SKU catalog by auto-extracted keywords.
"""

import datetime
import shutil
from pathlib import Path
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .config import OUTPUT_XLSX_PATH, RESULT_ARCHIVE_DIR
from .matcher import match_products_to_skus, extract_keywords
from .normalize import is_red_caviar

# ---------------------------------------------------------------------------
# Styles for XLSX
# ---------------------------------------------------------------------------
HEADER_FONT = Font(bold=True, size=11)
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
HEADER_FONT_WHITE = Font(bold=True, size=11, color="FFFFFF")
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
RED_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
YELLOW_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")

# ---------------------------------------------------------------------------
# ProductTracker
# ---------------------------------------------------------------------------
class ProductTracker:
    """Tracks products across runs using XLSX as persistent storage."""

    def __init__(self, skus: list[dict]):
        self.skus = skus
        self.matches: list[dict] = []     # product-SKU matches
        self.today = datetime.date.today().isoformat()

    # ------------------------------------------------------------------
    # Load existing XLSX
    # ------------------------------------------------------------------
    def load(self) -> bool:
        """Load existing result.xlsx. Returns True if file existed."""
        if not OUTPUT_XLSX_PATH.exists():
            return False

        try:
            from openpyxl import load_workbook
            wb = load_workbook(OUTPUT_XLSX_PATH)
            ws = wb["Совпадения"]

            headers = [cell.value for cell in ws[1]]
            self.matches = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0] is None:
                    continue
                match = dict(zip(headers, row))
                self.matches.append(match)

            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Проход 1: scrape → match → update / add
    # ------------------------------------------------------------------
    def merge_scraped(self, scraped_products: list[dict]) -> dict:
        """Match scraped products against SKUs, merge with existing table.

        Returns stats dict.
        """
        stats = {"new_matches": 0, "updated": 0, "skipped": 0}

        # Filter: only red caviar
        red_caviar = [p for p in scraped_products if is_red_caviar(p.get("name", ""))]
        if not red_caviar:
            return stats

        # Match products to SKUs
        new_matches = match_products_to_skus(red_caviar, self.skus)

        # Build index: url+sku → existing row
        existing_index: dict[str, int] = {}
        for i, m in enumerate(self.matches):
            key = f"{m.get('url', '')}|{m.get('matched_sku', '')}"
            if m.get("url"):
                existing_index[key] = i

        for nm in new_matches:
            key = f"{nm.get('url', '')}|{nm.get('matched_sku', '')}"
            if key in existing_index:
                # Update existing
                idx = existing_index[key]
                existing = self.matches[idx]

                # Skip if marked non-comparable
                if existing.get("is_comparable", "") == "нет":
                    stats["skipped"] += 1
                    continue

                # Update price, stock, status
                existing["product_price_rub"] = nm["product_price_rub"]
                existing["comparable_price_rub"] = nm["comparable_price_rub"]
                existing["in_stock"] = "да"
                existing["check_status"] = "да"
                stats["updated"] += 1
            else:
                # New match
                nm["check_status"] = "да"
                nm["is_comparable"] = ""
                self.matches.append(nm)
                stats["new_matches"] += 1

#        self._recheck_missing()
        return stats

    # ------------------------------------------------------------------
    # Проход 2: re-check missing (упрощённый — без HTTP-запросов)
    # ------------------------------------------------------------------
    def finalize(self) -> None:
        """Mark products not found in current scrape as unchecked."""
        for m in self.matches:
            if m.get("is_comparable") == "да" and m.get("check_status") != "да":
                m["check_status"] = "нет"

    # ------------------------------------------------------------------
    # Save XLSX
    # ------------------------------------------------------------------
    def save(self) -> Path:
        """Save to result.xlsx with 3 sheets. Returns path."""
        self.finalize()

        # Backup existing to archive
        if OUTPUT_XLSX_PATH.exists():
            RESULT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            backup = RESULT_ARCHIVE_DIR / f"result_{self.today}.xlsx"
            shutil.copy2(OUTPUT_XLSX_PATH, backup)

        wb = Workbook()

        # --- Sheet 1: Matches ---
        ws1 = wb.active
        ws1.title = "Совпадения"

        match_headers = [
            "site", "product_name", "product_weight_g", "product_price_rub",
            "matched_sku", "sku_weight_g", "comparable_price_rub",
            "our_price_rub", "in_stock", "check_status", "is_comparable", "url",
        ]
        self._write_sheet(ws1, match_headers, self.matches)

        # Column widths
        widths1 = [18, 45, 12, 12, 30, 10, 14, 12, 8, 8, 10, 40]
        for i, w in enumerate(widths1, 1):
            ws1.column_dimensions[get_column_letter(i)].width = w

        # Conditional: color rows by is_comparable
        for row_idx, m in enumerate(self.matches, 2):
            comp = m.get("is_comparable", "")
            if comp == "да":
                self._color_row(ws1, row_idx, len(match_headers), GREEN_FILL)
            elif comp == "нет":
                self._color_row(ws1, row_idx, len(match_headers), RED_FILL)

        # --- Sheet 2: Summary by SKU ---
        ws2 = wb.create_sheet("Сводка по SKU")

        summary_rows = self._build_summary()
        summary_headers = [
            "sku_name", "our_price_rub", "our_weight_g",
            "min_competitor_price", "avg_competitor_price", "max_competitor_price",
            "competitor_count", "our_vs_min_%",
        ]
        self._write_sheet(ws2, summary_headers, summary_rows)

        widths2 = [35, 12, 10, 14, 14, 14, 10, 12]
        for i, w in enumerate(widths2, 1):
            ws2.column_dimensions[get_column_letter(i)].width = w

        # Color: green if our price below min, red if above max
        for row_idx, sr in enumerate(summary_rows, 2):
            vs_min = sr.get("our_vs_min_%", 0) or 0
            if isinstance(vs_min, str):
                try:
                    vs_min = float(vs_min)
                except (ValueError, TypeError):
                    vs_min = 0
            if vs_min < 0:
                self._color_row(ws2, row_idx, len(summary_headers), GREEN_FILL)
            elif vs_min > 10:
                self._color_row(ws2, row_idx, len(summary_headers), RED_FILL)

        # --- Sheet 3: My SKUs ---
        ws3 = wb.create_sheet("Мои SKU")
        sku_headers = ["name", "our_price_rub", "weight_g", "keywords"]
        sku_rows = []
        for sku in self.skus:
            sku_rows.append({
                "name": sku["name"],
                "our_price_rub": sku.get("our_price_rub", 0),
                "weight_g": sku.get("weight_g", 0),
                "keywords": ", ".join(sku.get("keywords", [])),
            })
        self._write_sheet(ws3, sku_headers, sku_rows)

        widths3 = [40, 12, 10, 30]
        for i, w in enumerate(widths3, 1):
            ws3.column_dimensions[get_column_letter(i)].width = w

        # Save
        OUTPUT_XLSX_PATH.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(OUTPUT_XLSX_PATH))
        return OUTPUT_XLSX_PATH

    # ------------------------------------------------------------------
    # Summary builder
    # ------------------------------------------------------------------
    def _build_summary(self) -> list[dict]:
        """Build per-SKU summary: min/avg/max comparable price."""
        # Only include comparable products in stock
        comparable = [
            m for m in self.matches
            if m.get("is_comparable") == "да" and m.get("in_stock") == "да"
        ]

        by_sku: dict[str, dict] = {}
        for m in comparable:
            sku = m.get("matched_sku", "")
            price = float(m.get("comparable_price_rub", 0) or 0)
            if not sku or price <= 0:
                continue
            if sku not in by_sku:
                by_sku[sku] = {
                    "sku_name": sku,
                    "our_price_rub": m.get("our_price_rub", 0),
                    "our_weight_g": m.get("sku_weight_g", 0),
                    "prices": [],
                }
            by_sku[sku]["prices"].append(price)

        # Also include SKUs with no competitors
        for sku in self.skus:
            name = sku["name"]
            if name not in by_sku:
                by_sku[name] = {
                    "sku_name": name,
                    "our_price_rub": sku.get("our_price_rub", 0),
                    "our_weight_g": sku.get("weight_g", 0),
                    "prices": [],
                }

        rows = []
        for name, data in by_sku.items():
            prices = data["prices"]
            our = float(data["our_price_rub"] or 0)
            min_p = min(prices) if prices else 0
            avg_p = round(sum(prices) / len(prices), 2) if prices else 0
            max_p = max(prices) if prices else 0
            vs_min = round((our - min_p) / min_p * 100, 1) if min_p > 0 else 0

            rows.append({
                "sku_name": name,
                "our_price_rub": our,
                "our_weight_g": data["our_weight_g"],
                "min_competitor_price": min_p,
                "avg_competitor_price": avg_p,
                "max_competitor_price": max_p,
                "competitor_count": len(prices),
                "our_vs_min_%": vs_min,
            })

        # Sort: highest competitor_count first, then by name
        rows.sort(key=lambda r: (-r["competitor_count"], r["sku_name"]))
        return rows

    # ------------------------------------------------------------------
    # XLSX helpers
    # ------------------------------------------------------------------
    def _write_sheet(self, ws, headers: list[str], rows: list[dict]) -> None:
        """Write headers + rows to a worksheet with formatting."""
        # Headers
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT_WHITE
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
            cell.border = THIN_BORDER

        # Data
        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, h in enumerate(headers, 1):
                value = row_data.get(h, "")
                # Convert float → rounded for display
                if isinstance(value, float):
                    value = round(value, 2)
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.border = THIN_BORDER
                cell.alignment = Alignment(vertical="center")

        # Freeze header row
        ws.freeze_panes = "A2"
        # Auto-filter
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"

    @staticmethod
    def _color_row(ws, row_idx: int, col_count: int, fill) -> None:
        """Apply fill to all cells in a row."""
        for col in range(1, col_count + 1):
            ws.cell(row=row_idx, column=col).fill = fill

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_summary(self) -> dict:
        """Return summary counts."""
        comparable = [m for m in self.matches if m.get("is_comparable") == "да"]
        return {
            "total_matches": len(self.matches),
            "comparable": len(comparable),
            "non_comparable": sum(1 for m in self.matches if m.get("is_comparable") == "нет"),
            "new_unevaluated": sum(1 for m in self.matches if m.get("is_comparable", "") == ""),
        }


# ---------------------------------------------------------------------------
# Top-level helpers for cli.py
# ---------------------------------------------------------------------------
def run_tracker(scraped_all: list[dict], skus: list[dict]) -> ProductTracker:
    """Run the full tracking pipeline with multi-SKU matching.

    Returns ProductTracker with updated data (already saved to XLSX).
    """
    tracker = ProductTracker(skus=skus)

    # Load existing table
    existed = tracker.load()
    if existed:
        print(f"📂 Загружено {len(tracker.matches)} совпадений из result.xlsx\n")
    else:
        print("📂 result.xlsx не найден — будет создан новый файл\n")

    # --- Merge ---
    print(f"🔄 Сопоставление: {len(scraped_all)} продуктов × {len(skus)} SKU...")
    stats = tracker.merge_scraped(scraped_all)
    print(f"   Новых совпадений: {stats['new_matches']}")
    print(f"   Обновлено: {stats['updated']}")
    print(f"   Пропущено (не сопоставимы): {stats['skipped']}")

    # --- Save ---
    path = tracker.save()
    print(f"\n✅ Сохранено: {len(tracker.matches)} совпадений в {path}")

    return tracker


def print_tracker_report(tracker: ProductTracker) -> None:
    """Print a summary report from the tracker."""
    s = tracker.get_summary()
    comparable_by_sku = {}

    for m in tracker.matches:
        if m.get("is_comparable") == "да" and m.get("in_stock") == "да":
            sku = m.get("matched_sku", "")
            price = float(m.get("comparable_price_rub", 0) or 0)
            if sku not in comparable_by_sku:
                comparable_by_sku[sku] = []
            comparable_by_sku[sku].append(price)

    print(f"\n{'='*60}")
    print(f"📊 ОТЧЁТ — {tracker.today}")
    print(f"{'='*60}")
    print(f"\n📋 Всего совпадений: {s['total_matches']}")
    print(f"   Сопоставимых (да): {s['comparable']}")
    print(f"   Несопоставимых (нет): {s['non_comparable']}")
    print(f"   Новых (не оценено): {s['new_unevaluated']}")

    if comparable_by_sku:
        print(f"\n📈 Сводка по SKU (только сопоставимые в наличии):\n")
        print(f"{'SKU':<30} {'Наша':>8} {'Мин':>8} {'Сред':>8} {'Макс':>8} {'Кол-во':>7} {'vs мин':>7}")
        print(f"{'-'*30} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*7}")

        for sku_name, prices in sorted(comparable_by_sku.items()):
            if not prices:
                continue
            our = 0
            for sku in tracker.skus:
                if sku["name"] == sku_name:
                    our = sku.get("our_price_rub", 0)
                    break
            mn = min(prices)
            avg = sum(prices) / len(prices)
            mx = max(prices)
            vs = f"{(our - mn) / mn * 100:+.0f}%" if mn > 0 else "—"
            print(f"{sku_name[:30]:<30} {our:>8,.0f} {mn:>8,.0f} {avg:>8,.0f} {mx:>8,.0f} {len(prices):>7} {vs:>7}".replace(",", " "))

    new_count = s.get("new_unevaluated", 0)
    if new_count > 0:
        print(f"\n📝 {new_count} новых совпадений ждут оценки.")
        print(f"   Открой {OUTPUT_XLSX_PATH} → лист «Совпадения»")
        print(f"   и поставь 'да' или 'нет' в колонке is_comparable.")
