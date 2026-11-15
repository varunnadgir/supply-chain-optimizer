# Data

This project uses the **Brazilian E-Commerce Public Dataset by Olist**: about 100,000 real orders placed on the Olist marketplace between 2016 and 2018, released by Olist on Kaggle under the CC BY-NC-SA 4.0 license.

The raw files are not stored in this repository because they are large (about 125 MB). To get them:

1. Download the dataset from Kaggle: <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce> (free account needed).
2. Unzip it and put the nine CSV files in `data/raw/`:

```
data/raw/olist_customers_dataset.csv
data/raw/olist_geolocation_dataset.csv
data/raw/olist_order_items_dataset.csv
data/raw/olist_order_payments_dataset.csv
data/raw/olist_order_reviews_dataset.csv
data/raw/olist_orders_dataset.csv
data/raw/olist_products_dataset.csv
data/raw/olist_sellers_dataset.csv
data/raw/product_category_name_translation.csv
```

3. From the repository root, build the SQLite database:

```
python src/build_database.py
```

That creates `data/olist.db` with these tables:

| Table | Rows | One row is |
|---|---|---|
| `orders` | 99,441 | an order (status and timestamps) |
| `order_items` | 112,650 | an item inside an order (price, freight, seller) |
| `customers` | 99,441 | the customer of an order (city, state, zip prefix) |
| `sellers` | 3,095 | a seller (city, state, zip prefix) |
| `products` | 32,951 | a product (category, weight, size) |
| `payments` | 103,886 | a payment on an order |
| `reviews` | 99,224 | a customer review of an order |
| `geolocation` | 1,000,163 | a latitude and longitude point for a zip prefix |
| `category_translation` | 71 | Portuguese category name to English |
| `zip_geo` | 19,010 | built by the script: one average location per zip prefix |
