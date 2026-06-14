WITH item_category_freq AS (
    SELECT
        item_desc   AS item_name,
        category,
        COUNT(*)    AS freq
    FROM {{ ref('stg_transactions') }}
    GROUP BY 1, 2
),

-- One row per item: pick the most frequently recorded category
-- to resolve inconsistent categorisation in the source data.
canonical_items AS (
    SELECT DISTINCT ON (item_name)
        item_name,
        category
    FROM item_category_freq
    ORDER BY item_name, freq DESC
),

with_surrogate_key AS (
    SELECT
        printf('ITM%03d', ROW_NUMBER() OVER (ORDER BY item_name)) AS item_id,
        item_name,
        category
    FROM canonical_items
),

with_promo AS (
    SELECT
        i.item_id,
        i.item_name,
        i.category,
        p.promotion_type,
        p.stock_multiplier
    FROM with_surrogate_key i
    LEFT JOIN {{ ref('ref_promotional_items') }} p ON i.item_name = p.item_name
)

SELECT * FROM with_promo
