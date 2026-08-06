"""Simulate user curation: mark products as comparable/not."""
import csv

rows = []
with open("data/products.csv", "r", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

yes_count = 0
no_count = 0

for r in rows:
    name = r.get("product_name", "")
    price = r.get("original_price_rub", "0")
    w = float(r.get("weight_g", 0) or 0)

    # Only consider gorbusha (pink salmon) as comparable
    if "gorbush" in name.lower() or "горбуш" in name.lower():
        if 80 <= w <= 200:
            r["is_comparable"] = "да"
            yes_count += 1
        else:
            r["is_comparable"] = "нет"
            no_count += 1
    elif r["is_comparable"] == "":
        r["is_comparable"] = "нет"
        no_count += 1

# Save
with open("data/products.csv", "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"Marked: да={yes_count}, нет={no_count}")
print("Saved data/products.csv")
