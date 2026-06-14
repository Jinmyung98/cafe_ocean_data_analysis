WITH stg AS (
    SELECT * FROM {{ ref('stg_transactions') }}
),

items AS (
    SELECT * FROM {{ ref('dim_items') }}
),

joined AS (
    SELECT
        stg.bill_number,
        stg.date,
        stg.transaction_time,
        stg.time_parse_failed,
        items.item_id,
        stg.quantity,
        stg.rate,
        stg.tax,
        stg.discount,
        stg.total
    FROM stg
    LEFT JOIN items ON stg.item_desc = items.item_name
),

with_surrogate_key AS (
    SELECT
        printf('TXN%06d', ROW_NUMBER() OVER (
            ORDER BY date, bill_number, transaction_time, item_id
        ))                  AS transaction_id,
        bill_number,
        date,
        transaction_time,
        time_parse_failed,
        item_id,
        quantity,
        rate,
        tax,
        discount,
        total
    FROM joined
)

SELECT * FROM with_surrogate_key
