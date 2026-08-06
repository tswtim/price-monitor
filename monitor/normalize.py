"""Weight extraction and price normalization for caviar products."""

import re
import hashlib
from typing import Optional


# --- Weight extraction ---

WEIGHT_PATTERNS = [
    # "120 г", "120г", "120 гр", "120 грамм"
    re.compile(r"(\d{1,4})\s*(?:г|гр|грамм)\b", re.IGNORECASE),
    # "0.12 кг", "0,12 кг", "0.12кг"
    re.compile(r"(\d+[.,]\d+)\s*(?:кг|kg)\b", re.IGNORECASE),
    # "1 кг", "1кг"
    re.compile(r"(\d{1,2})\s*(?:кг|kg)\b", re.IGNORECASE),
]

# Patterns to identify caviar type
RED_CAVIAR_KEYWORDS = [
    "горбуш",   # горбуша / горбуши
    "кета",     # chum salmon
    "нерка",    # sockeye
    "кижуч",    # coho
    "лосос",    # salmon (general)
    "форел",    # trout
    "красн.*икр",  # red caviar
]

EXCLUDE_KEYWORDS = [
    "ч[её]рн.*икр",     # black caviar
    "морск.*еж",        # sea urchin
    "овощн.*икр",       # vegetable caviar
    "кабачк.*икр",      # squash caviar
    "баклажан.*икр",    # eggplant caviar
    "тобико",           # tobiko
    "масаго",           # masago
    "щук",              # pike caviar
    "минта",            # pollock caviar
    "треск",            # cod caviar
    "мойв",             # capelin caviar
    "частик",           # "chastik" (mixed)
    "пробойн",          # punched caviar
    "имитир",           # imitation
]


def extract_weight_grams(name: str, quantity: Optional[float] = None) -> Optional[float]:
    """Extract product weight in grams from the product name or quantity field.

    Args:
        name: Product name/description
        quantity: Optional quantity in kg (e.g., 0.1 = 100g, from apeti.ru data-quantity)
    """
    # If quantity is provided in kg, convert to grams
    if quantity is not None and quantity > 0:
        return round(quantity * 1000, 1)

    # Try regex patterns
    for pattern in WEIGHT_PATTERNS:
        match = pattern.search(name)
        if match:
            value = float(match.group(1).replace(",", "."))
            if "кг" in match.group(0).lower() or "kg" in match.group(0).lower():
                return round(value * 1000, 1)
            return value

    return None


# --- Product matching ---

def is_red_caviar(name: str) -> bool:
    """Check if the product is red caviar (salmon caviar)."""
    name_lower = name.lower()

    # Exclude non-red caviar first
    for pattern in EXCLUDE_KEYWORDS:
        if re.search(pattern, name_lower):
            return False

    # Check if it matches red caviar
    for kw in RED_CAVIAR_KEYWORDS:
        if kw in name_lower:
            return True
    return False


def is_target_species(name: str, keywords: list[str]) -> bool:
    """Check if product matches the target species (e.g., pink salmon / горбуша)."""
    name_lower = name.lower()
    return any(kw.lower() in name_lower for kw in keywords)


def match_tier(weight_g: Optional[float], target_weight_g: float) -> str:
    """Classify how closely the product matches the target weight."""
    if weight_g is None:
        return "wide"
    diff = abs(weight_g - target_weight_g)
    if diff <= 5:
        return "exact"
    if diff <= 50:
        return "close"
    return "wide"


# --- Price normalization ---

def compute_unit_prices(price_rub: float, weight_g: float) -> tuple[float, float]:
    """Compute price per 100g and price per kg."""
    if weight_g <= 0:
        return (0.0, 0.0)
    price_per_100g = round(price_rub / weight_g * 100, 2)
    price_per_kg = round(price_rub / weight_g * 1000, 2)
    return (price_per_100g, price_per_kg)


def safe_int_price(raw: str) -> Optional[int]:
    """Parse a price string like '1 485', '1485', '1,485.00' to int."""
    if not raw:
        return None
    # Remove spaces, currency symbols
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    # Replace comma with dot if it's a decimal separator
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


# --- Product identity ---

def make_product_key(site: str, name: str, weight_g: Optional[float]) -> str:
    """Create a stable key for tracking the same product across runs."""
    normalized = re.sub(r"\s+", " ", name.lower().strip())
    normalized = re.sub(r"[^\w\s]", "", normalized)
    base = f"{site}|{normalized}|{weight_g or 0}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]


# --- Brand extraction ---

KNOWN_BRANDS = [
    "Икорный", "Кристалл", "Русский икорный дом", "Русский океан",
    "Скайф", "Путина", "Сахалин", "Камчатка", "Доброфлот",
    "Русский рыбный мир", "Меридиан", "Балтийский берег",
    "Тунгутун", "НЕРЕСТ", "Caviar", "Лукоморье",
]


def extract_brand(name: str) -> str:
    """Try to extract brand name from product name."""
    name_lower = name.lower()
    for brand in KNOWN_BRANDS:
        if brand.lower() in name_lower:
            return brand
    return ""


# --- Bulk normalization ---

def normalize_product(raw: dict, target: dict) -> Optional[dict]:
    """Normalize a raw product dict into the standard format.

    Args:
        raw: Raw product data from adapter
        target: Target product config {'name': ..., 'weight_g': ..., 'keywords': [...]}

    Returns:
        Normalized dict or None if the product should be excluded
    """
    name = raw.get("name", "")
    if not name:
        return None

    # Filter: must be red caviar
    if not is_red_caviar(name):
        return None

    # Extract / compute
    brand = raw.get("brand") or extract_brand(name)
    weight_g = raw.get("weight_g")

    # Try to extract weight from name if not provided
    if weight_g is None:
        weight_g = extract_weight_grams(name)

    price_rub = raw.get("price_rub", 0)
    if not price_rub or price_rub <= 0:
        return None

    # Compute unit prices
    if weight_g and weight_g > 0:
        price_per_100g, price_per_kg = compute_unit_prices(price_rub, weight_g)
    else:
        price_per_100g = None
        price_per_kg = None

    # Match tier
    keywords = target.get("keywords", [])
    tier = "wide"
    if is_target_species(name, keywords):
        tier = match_tier(weight_g, target.get("weight_g", 120))
    elif not is_red_caviar(name):
        return None  # already filtered above, but double-check

    # Sanity check: price per kg should be in reasonable range
    if price_per_kg and (price_per_kg < 1000 or price_per_kg > 80000):
        # Suspicious — but keep it, just flag
        pass

    product_key = make_product_key(raw.get("site", ""), name, weight_g)

    return {
        "site": raw.get("site", ""),
        "product_id": raw.get("product_id", ""),
        "name": name,
        "brand": brand,
        "weight_g": weight_g,
        "price_rub": price_rub,
        "price_per_100g": price_per_100g,
        "price_per_kg": price_per_kg,
        "in_stock": raw.get("in_stock", 1),
        "url": raw.get("url", ""),
        "match_tier": tier,
        "product_key": product_key,
        "raw": raw.get("raw", {}),
    }


def normalize_batch(raw_products: list[dict], target: dict) -> list[dict]:
    """Normalize a batch of raw products for a single target."""
    results = []
    for p in raw_products:
        norm = normalize_product(p, target)
        if norm:
            results.append(norm)
    return results
