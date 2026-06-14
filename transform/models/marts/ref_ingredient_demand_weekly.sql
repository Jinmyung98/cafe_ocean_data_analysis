{{ config(materialized='table') }}

-- Weekly ingredient consumption derived from transaction history × BOM.
-- Promotional items (1+1, 2+1, BUNDLE) are corrected via stock_multiplier
-- from ref_promotional_items so that stock consumed reflects actual units
-- dispensed, not just the billed quantity.

WITH weekly_usage AS (
    SELECT
        YEAR(ft.date)                                                          AS yr,
        WEEK(ft.date)                                                          AS wk,
        bom.ingredient_id,
        SUM(
            ft.quantity
            * bom.quantity_per_item
            * COALESCE(di.stock_multiplier, 1)
        )                                                                      AS units_used
    FROM {{ ref('fact_transactions') }}      ft
    JOIN {{ ref('dim_items') }}              di  ON ft.item_id    = di.item_id
    JOIN {{ ref('bridge_bill_of_materials') }} bom ON di.item_id  = bom.item_id
    WHERE ft.time_parse_failed = false
    GROUP BY 1, 2, 3
)

SELECT
    wu.ingredient_id,
    di_ing.ingredient_name,
    di_ing.unit,
    di_ing.shelf_life_days,
    di_ing.purchase_unit_name,
    di_ing.purchase_unit_size,
    di_ing.purchase_unit_cost,
    di_ing.supplier_id,
    ROUND(AVG(wu.units_used), 1)                                               AS avg_weekly_units,
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY wu.units_used), 1)      AS p75_weekly_units,
    COUNT(*)                                                                   AS weeks_observed
FROM weekly_usage wu
JOIN {{ ref('dim_ingredients') }} di_ing ON wu.ingredient_id = di_ing.ingredient_id
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
ORDER BY wu.ingredient_id
