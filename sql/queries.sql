-- Business questions for the Olist supply chain project.
-- Database: data/olist.db (SQLite), built by src/build_database.py.
--
-- Every query starts with a "-- name:" line. The notebooks load a query by
-- that name (see run_query in src/helpers.py), so this file is the single
-- place where the SQL lives.
--
-- "The study year" below means orders placed from 2017-09-01 to 2018-08-31,
-- the most recent 12 full months in the data.


-- name: monthly_trend
-- Q1. How fast is the business growing? Orders, revenue and freight per month.
SELECT
    strftime('%Y-%m', o.order_purchase_timestamp)   AS month,
    COUNT(DISTINCT o.order_id)                      AS orders,
    ROUND(SUM(oi.price), 0)                         AS revenue,
    ROUND(SUM(oi.freight_value), 0)                 AS freight
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2017-01-01'
  AND o.order_purchase_timestamp <  '2018-09-01'
GROUP BY month
ORDER BY month;


-- name: demand_by_state
-- Q2. Where are the customers? Orders and freight burden by customer state.
-- The window function SUM(...) OVER () gives the national total on every row,
-- which lets us compute each state's share without a second query.
SELECT
    c.customer_state                                           AS state,
    COUNT(DISTINCT o.order_id)                                 AS orders,
    ROUND(100.0 * COUNT(DISTINCT o.order_id)
          / SUM(COUNT(DISTINCT o.order_id)) OVER (), 1)        AS pct_of_orders,
    ROUND(SUM(oi.price), 0)                                    AS revenue,
    ROUND(AVG(oi.freight_value), 2)                            AS avg_freight_per_item,
    ROUND(100.0 * SUM(oi.freight_value) / SUM(oi.price), 1)    AS freight_pct_of_price
FROM orders o
JOIN customers   c  ON c.customer_id = o.customer_id
JOIN order_items oi ON oi.order_id   = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2017-09-01'
  AND o.order_purchase_timestamp <  '2018-09-01'
GROUP BY c.customer_state
ORDER BY orders DESC;


-- name: supply_vs_demand
-- Q3. Is supply located where demand is? Share of items SOLD FROM each state
-- next to the share of items SHIPPED TO each state. Two CTEs, then a join.
WITH study_items AS (
    SELECT oi.seller_id, o.customer_id
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= '2017-09-01'
      AND o.order_purchase_timestamp <  '2018-09-01'
),
supply AS (
    SELECT s.seller_state AS state, COUNT(*) AS items_shipped_from
    FROM study_items si
    JOIN sellers s ON s.seller_id = si.seller_id
    GROUP BY s.seller_state
),
demand AS (
    SELECT c.customer_state AS state, COUNT(*) AS items_shipped_to
    FROM study_items si
    JOIN customers c ON c.customer_id = si.customer_id
    GROUP BY c.customer_state
)
SELECT
    d.state,
    ROUND(100.0 * COALESCE(s.items_shipped_from, 0)
          / (SELECT COUNT(*) FROM study_items), 1)   AS pct_of_supply,
    ROUND(100.0 * d.items_shipped_to
          / (SELECT COUNT(*) FROM study_items), 1)   AS pct_of_demand
FROM demand d
LEFT JOIN supply s ON s.state = d.state
ORDER BY pct_of_demand DESC;


-- name: delivery_by_state
-- Q4. How long do customers wait, and how often is the order late?
-- julianday() turns a timestamp into a day number, so subtracting two of them
-- gives elapsed days. "Late" means delivered after the date Olist promised.
SELECT
    c.customer_state                                             AS state,
    COUNT(*)                                                     AS orders,
    ROUND(AVG(julianday(o.order_delivered_customer_date)
            - julianday(o.order_purchase_timestamp)), 1)         AS avg_days_to_deliver,
    ROUND(AVG(julianday(o.order_estimated_delivery_date)
            - julianday(o.order_purchase_timestamp)), 1)         AS avg_days_promised,
    ROUND(100.0 * AVG(CASE
            WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date
            THEN 1 ELSE 0 END), 1)                               AS pct_late
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_purchase_timestamp >= '2017-09-01'
  AND o.order_purchase_timestamp <  '2018-09-01'
GROUP BY c.customer_state
ORDER BY avg_days_to_deliver DESC;


-- name: same_state_vs_cross_state
-- Q5. What does distance cost? Compare items that stay inside one state with
-- items that cross a state line.
SELECT
    CASE WHEN s.seller_state = c.customer_state
         THEN 'same state' ELSE 'cross state' END                AS lane_type,
    COUNT(*)                                                     AS items,
    ROUND(AVG(oi.freight_value), 2)                              AS avg_freight,
    ROUND(AVG(julianday(o.order_delivered_customer_date)
            - julianday(o.order_delivered_carrier_date)), 1)     AS avg_transit_days,
    ROUND(100.0 * AVG(CASE
            WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date
            THEN 1 ELSE 0 END), 1)                               AS pct_late
FROM order_items oi
JOIN orders    o ON o.order_id    = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
JOIN sellers   s ON s.seller_id   = oi.seller_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_carrier_date  IS NOT NULL
  AND o.order_purchase_timestamp >= '2017-09-01'
  AND o.order_purchase_timestamp <  '2018-09-01'
GROUP BY lane_type;


-- name: top_lanes
-- Q6. Which shipping lanes (seller state -> customer state) carry the most volume?
SELECT
    s.seller_state || ' -> ' || c.customer_state                 AS lane,
    COUNT(*)                                                     AS items,
    ROUND(AVG(oi.freight_value), 2)                              AS avg_freight,
    ROUND(AVG(julianday(o.order_delivered_customer_date)
            - julianday(o.order_delivered_carrier_date)), 1)     AS avg_transit_days
FROM order_items oi
JOIN orders    o ON o.order_id    = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
JOIN sellers   s ON s.seller_id   = oi.seller_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_carrier_date  IS NOT NULL
  AND o.order_purchase_timestamp >= '2017-09-01'
  AND o.order_purchase_timestamp <  '2018-09-01'
GROUP BY lane
ORDER BY items DESC
LIMIT 12;


-- name: top_categories
-- Q7. What do we sell the most of, and which categories are expensive to ship?
-- RANK() OVER (...) numbers the categories from best seller down.
SELECT
    RANK() OVER (ORDER BY COUNT(*) DESC)                         AS rank_by_units,
    COALESCE(t.product_category_name_english,
             p.product_category_name, 'unknown')                 AS category,
    COUNT(*)                                                     AS units,
    ROUND(SUM(oi.price), 0)                                      AS revenue,
    ROUND(AVG(oi.price), 2)                                      AS avg_price,
    ROUND(AVG(p.product_weight_g) / 1000.0, 2)                   AS avg_weight_kg,
    ROUND(100.0 * SUM(oi.freight_value) / SUM(oi.price), 1)      AS freight_pct_of_price
FROM order_items oi
JOIN orders   o ON o.order_id   = oi.order_id
JOIN products p ON p.product_id = oi.product_id
LEFT JOIN category_translation t
       ON t.product_category_name = p.product_category_name
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2017-09-01'
  AND o.order_purchase_timestamp <  '2018-09-01'
GROUP BY category
ORDER BY units DESC
LIMIT 15;


-- name: late_delivery_vs_reviews
-- Q8. Does slow delivery hurt customer satisfaction? Review score by how the
-- delivery went. An order can have more than one review, so the CTE averages
-- them to one score per order first.
WITH order_score AS (
    SELECT order_id, AVG(review_score) AS score
    FROM reviews
    GROUP BY order_id
)
SELECT
    CASE
        WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date
             AND julianday(o.order_delivered_customer_date)
               - julianday(o.order_purchase_timestamp) <= 7  THEN '1. on time, within a week'
        WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date
                                                             THEN '2. on time, over a week'
        ELSE                                                      '3. late'
    END                                                          AS delivery_outcome,
    COUNT(*)                                                     AS orders,
    ROUND(AVG(r.score), 2)                                       AS avg_review_score,
    ROUND(100.0 * AVG(CASE WHEN r.score <= 2 THEN 1 ELSE 0 END), 1) AS pct_bad_reviews
FROM orders o
JOIN order_score r ON r.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_purchase_timestamp >= '2017-09-01'
  AND o.order_purchase_timestamp <  '2018-09-01'
GROUP BY delivery_outcome
ORDER BY delivery_outcome;


-- name: shipments
-- The base table for the network model (notebook 2): one row per item sold in
-- the study year, with where it shipped from, where it went, what it weighed
-- and what the freight cost. Six tables joined into one.
SELECT
    oi.order_id,
    oi.seller_id,
    oi.price,
    oi.freight_value,
    p.product_weight_g / 1000.0             AS weight_kg,
    c.customer_city,
    c.customer_state,
    cg.lat                                  AS cust_lat,
    cg.lng                                  AS cust_lng,
    s.seller_state,
    sg.lat                                  AS seller_lat,
    sg.lng                                  AS seller_lng,
    julianday(o.order_delivered_customer_date)
      - julianday(o.order_delivered_carrier_date) AS transit_days,
    o.order_purchase_timestamp              AS purchased_at
FROM order_items oi
JOIN orders    o  ON o.order_id    = oi.order_id
JOIN customers c  ON c.customer_id = o.customer_id
JOIN sellers   s  ON s.seller_id   = oi.seller_id
JOIN products  p  ON p.product_id  = oi.product_id
JOIN zip_geo   cg ON cg.zip_prefix = c.customer_zip_code_prefix
JOIN zip_geo   sg ON sg.zip_prefix = s.seller_zip_code_prefix
WHERE o.order_status = 'delivered'
  AND p.product_weight_g IS NOT NULL
  AND o.order_purchase_timestamp >= '2017-09-01'
  AND o.order_purchase_timestamp <  '2018-09-01';


-- name: weekly_category_demand
-- The base table for the inventory model (notebook 3): units sold per week,
-- per category, per customer city. Weeks start on Monday.
-- 'weekday 0' moves a date forward to Sunday, and '-6 days' steps back to the
-- Monday of that same week.
-- The data ends on 2018-08-19 here on purpose: order counts in the dataset
-- fade out after about 22 Aug 2018, so the final week is incomplete.
SELECT
    date(o.order_purchase_timestamp, 'weekday 0', '-6 days')    AS week_start,
    COALESCE(t.product_category_name_english,
             p.product_category_name, 'unknown')                AS category,
    c.customer_city,
    c.customer_state,
    COUNT(*)                                                    AS units,
    ROUND(SUM(oi.price), 2)                                     AS revenue
FROM order_items oi
JOIN orders    o ON o.order_id    = oi.order_id
JOIN customers c ON c.customer_id = o.customer_id
JOIN products  p ON p.product_id  = oi.product_id
LEFT JOIN category_translation t
       ON t.product_category_name = p.product_category_name
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2017-01-02'
  AND o.order_purchase_timestamp <  '2018-08-20'
GROUP BY week_start, category, c.customer_city, c.customer_state;


-- name: moving_average_demo
-- Bonus: a 4-week moving average written directly in SQL with a window
-- function. Notebook 3 does the same thing in pandas and compares the two.
WITH weekly AS (
    SELECT
        date(o.order_purchase_timestamp, 'weekday 0', '-6 days') AS week_start,
        COUNT(*)                                                 AS units
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= '2017-01-02'
      AND o.order_purchase_timestamp <  '2018-08-20'
    GROUP BY week_start
)
SELECT
    week_start,
    units,
    ROUND(AVG(units) OVER (
        ORDER BY week_start
        ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
    ), 1) AS moving_avg_4wk
FROM weekly
ORDER BY week_start;
