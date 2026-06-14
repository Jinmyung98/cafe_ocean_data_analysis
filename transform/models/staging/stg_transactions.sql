WITH source AS (
    SELECT * FROM {{ source('raw', 'raw_transactions') }}
),

staged AS (
    SELECT
        -- identifiers
        bill_number,

        -- timestamps
        CAST(date AS DATE)                                          AS date,
        TRY_CAST(transaction_time AS TIME)                         AS transaction_time,
        CASE
            WHEN TRY_CAST(transaction_time AS TIME) IS NULL
             AND transaction_time IS NOT NULL
            THEN true ELSE false
        END                                                         AS time_parse_failed,

        -- product
        item_desc,
        category,

        -- financials
        quantity,
        rate,
        tax,
        discount,
        total

    FROM source
)

SELECT * FROM staged
