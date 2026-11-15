"""Build data/olist.db (SQLite) from the nine raw Olist CSV files.

Run from the repo root:

    python src/build_database.py

What it does:
  1. Loads each CSV in data/raw/ into its own table (short, readable names).
  2. Adds indexes on the columns we join on, so queries run fast.
  3. Builds one helper table in SQL, `zip_geo`, with a single latitude and
     longitude per zip code prefix. The raw geolocation table has about a
     million rows with many points per zip prefix, so we average them.
"""

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "olist.db"

# csv file name -> table name in the database
TABLES = {
    "olist_customers_dataset.csv": "customers",
    "olist_sellers_dataset.csv": "sellers",
    "olist_orders_dataset.csv": "orders",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "payments",
    "olist_order_reviews_dataset.csv": "reviews",
    "olist_products_dataset.csv": "products",
    "olist_geolocation_dataset.csv": "geolocation",
    "product_category_name_translation.csv": "category_translation",
}

# Zip code prefixes look like numbers ("01037") but they are labels.
# Reading them as text keeps the leading zeros.
TEXT_COLUMNS = {
    "customer_zip_code_prefix": str,
    "seller_zip_code_prefix": str,
    "geolocation_zip_code_prefix": str,
}

INDEXES = [
    "CREATE INDEX idx_orders_customer ON orders(customer_id)",
    "CREATE INDEX idx_orders_id ON orders(order_id)",
    "CREATE INDEX idx_items_order ON order_items(order_id)",
    "CREATE INDEX idx_items_product ON order_items(product_id)",
    "CREATE INDEX idx_items_seller ON order_items(seller_id)",
    "CREATE INDEX idx_customers_id ON customers(customer_id)",
    "CREATE INDEX idx_sellers_id ON sellers(seller_id)",
    "CREATE INDEX idx_products_id ON products(product_id)",
    "CREATE INDEX idx_reviews_order ON reviews(order_id)",
    "CREATE INDEX idx_payments_order ON payments(order_id)",
]

# One row per zip prefix. The WHERE clause throws out a handful of bad points
# that fall outside Brazil (the raw table has a few in Europe and the ocean).
ZIP_GEO_SQL = """
CREATE TABLE zip_geo AS
SELECT
    geolocation_zip_code_prefix AS zip_prefix,
    AVG(geolocation_lat)        AS lat,
    AVG(geolocation_lng)        AS lng,
    COUNT(*)                    AS n_points
FROM geolocation
WHERE geolocation_lat BETWEEN -34.0 AND 5.5
  AND geolocation_lng BETWEEN -74.0 AND -34.5
GROUP BY geolocation_zip_code_prefix
"""


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()  # always rebuild from scratch so the result is repeatable

    con = sqlite3.connect(DB_PATH)
    for csv_name, table in TABLES.items():
        path = RAW / csv_name
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}. Download the dataset first (see data/README.md)."
            )
        df = pd.read_csv(path, dtype=TEXT_COLUMNS, encoding="utf-8-sig")
        df.to_sql(table, con, index=False)
        print(f"{table:22s} {len(df):>9,} rows")

    for statement in INDEXES:
        con.execute(statement)

    con.execute(ZIP_GEO_SQL)
    con.execute("CREATE INDEX idx_zip_geo ON zip_geo(zip_prefix)")
    n_zip = con.execute("SELECT COUNT(*) FROM zip_geo").fetchone()[0]
    print(f"{'zip_geo (derived)':22s} {n_zip:>9,} rows")

    con.commit()
    con.close()
    print(f"\nDatabase written to {DB_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
