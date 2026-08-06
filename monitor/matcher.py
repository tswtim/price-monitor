"""SKU keyword extraction and product-to-SKU matching.

Auto-extracts search keywords from SKU names using simple Russian stemming.
Matches scraped products against SKUs by keyword presence.
"""

import re
from pathlib import Path
from typing import Optional

from .config import SKUS_PATH

# ---------------------------------------------------------------------------
# Russian stemming: remove common endings to get word roots
# ---------------------------------------------------------------------------
# These are ordered from longest to shortest to avoid partial matches
ENDINGS = [
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими",
    "ах", "ях", "ов", "ев", "ам", "ям", "ой", "ей", "ый", "ий",
    "ая", "яя", "ое", "ее", "ые", "ие", "ых", "их", "ую", "юю",
    "а", "я", "ы", "и", "у", "ю", "е", "о", "ь", "ъ",
]

# Words to exclude from keywords (too generic or not useful for search)
STOP_WORDS = {
    "для", "под", "над", "без", "при", "под", "перед", "через",
    "наш", "ваш", "свой", "весь", "этот", "тот",
    "банк", "банка", "упак", "упаковка", "уп",
    "жб", "ж/б", "стекло", "стекл", "пластик",
    "премиум", "элит", "отбор", "высш", "перв",
    "сорт", "класс", "категор",
}


def stem_word(word: str) -> str:
    """Remove common Russian endings from a word to get its root."""
    word_lower = word.lower()
    for ending in ENDINGS:
        if word_lower.endswith(ending) and len(word_lower) - len(ending) >= 3:
            return word_lower[:-len(ending)]
    return word_lower


def extract_keywords(name: str) -> list[str]:
    """Extract search keywords from a product/SKU name.

    Example:
        "Икра горбуши 1/120" → ["горбуш", "икр"]
        "Икра кеты 1/140 ж/б" → ["кет", "икр"]
    """
    # Remove numbers, fractions, measure units
    cleaned = name.lower()
    cleaned = re.sub(r'\d+/\d+', ' ', cleaned)       # 1/120
    cleaned = re.sub(r'\d+[,.]?\d*\s*(?:г|гр|грамм|кг|мл|л|шт)\b', ' ', cleaned)
    cleaned = re.sub(r'\d+', ' ', cleaned)            # standalone numbers
    cleaned = re.sub(r'[^\w\s]', ' ', cleaned)        # punctuation
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # Split into words
    words = cleaned.split()

    # Stem each word
    stems = [stem_word(w) for w in words]

    # Filter: min 3 chars, not a stop word
    keywords = []
    for s in stems:
        if len(s) >= 3 and s not in STOP_WORDS:
            keywords.append(s)

    # Remove duplicates while preserving order
    seen = set()
    unique = []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            unique.append(k)

    return unique


# ---------------------------------------------------------------------------
# SKU loading and matching
# ---------------------------------------------------------------------------
def load_skus(path: Optional[Path] = None) -> list[dict]:
    """Load SKU definitions from XLSX.

    XLSX columns: name, our_price_rub, weight_g

    Returns list of dicts with added 'keywords' field.
    """
    path = path or SKUS_PATH
    if not path.exists():
        return []

    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb.active

    # Read headers from row 1
    headers = [cell.value for cell in ws[1]]
    name_col = headers.index("name") if "name" in headers else 0
    price_col = headers.index("our_price_rub") if "our_price_rub" in headers else 1
    weight_col = headers.index("weight_g") if "weight_g" in headers else 2

    skus = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = str(row[name_col] or "").strip() if name_col < len(row) else ""
        if not name:
            continue
        price = float(row[price_col] or 0) if price_col < len(row) else 0.0
        weight = float(row[weight_col] or 0) if weight_col < len(row) else 0.0
        skus.append({
            "name": name,
            "our_price_rub": price,
            "weight_g": weight,
            "keywords": extract_keywords(name),
        })
    return skus


def create_skus_template(path: Optional[Path] = None) -> Path:
    """Create a template my_skus.xlsx with examples if it doesn't exist."""
    path = path or SKUS_PATH
    if path.exists():
        return path

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Мои SKU"

    # Header
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    for col, h in enumerate(["name", "our_price_rub", "weight_g"], 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill

    # Examples
    examples = [
        ["Икра горбуши (1/120)", 1590, 120],
        ["Икра горбуши (1/250)", 2900, 250],
        ["Икра кеты 1/140", 2100, 140],
        ["Икра нерки 500г", 1800, 100],
    ]
    for r, row_data in enumerate(examples, 2):
        for c, val in enumerate(row_data, 1):
            ws.cell(row=r, column=c, value=val)

    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 10
    ws.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


# ---------------------------------------------------------------------------
# Product → SKU matching
# ---------------------------------------------------------------------------
def match_products_to_skus(
    products: list[dict],
    skus: list[dict],
) -> list[dict]:
    """Match scraped products against SKUs by keyword presence.

    Args:
        products: list of normalized product dicts (with name, price, weight, url, etc.)
        skus: list of SKU dicts with 'keywords' field

    Returns:
        list of match dicts (one per product-SKU pair):
            site, product_name, product_weight_g, product_price_rub,
            matched_sku, sku_weight_g, comparable_price_rub,
            our_price_rub, in_stock, check_status, is_comparable, url
    """
    matches = []

    for product in products:
        product_name = product.get("name", "").lower()
        if not product_name:
            continue

        product_weight = float(product.get("weight_g", 0) or 0)
        product_price = float(product.get("price_rub", 0) or 0)

        if product_weight <= 0 or product_price <= 0:
            continue

        # Check each SKU
        for sku in skus:
            keywords = sku.get("keywords", [])
            if not keywords:
                continue

            # ALL keywords must be present in the product name
            if all(kw in product_name for kw in keywords):
                sku_weight = sku.get("weight_g", 0)
                comparable = round(product_price / product_weight * sku_weight, 2) if sku_weight > 0 else 0

                matches.append({
                    "site": product.get("site", ""),
                    "product_name": product.get("name", ""),
                    "product_weight_g": product_weight,
                    "product_price_rub": product_price,
                    "matched_sku": sku["name"],
                    "sku_weight_g": sku_weight,
                    "comparable_price_rub": comparable,
                    "our_price_rub": sku.get("our_price_rub", 0),
                    "in_stock": product.get("in_stock", "да"),
                    "check_status": product.get("check_status", ""),
                    "is_comparable": product.get("is_comparable", ""),
                    "url": product.get("url", ""),
                })

    return matches
