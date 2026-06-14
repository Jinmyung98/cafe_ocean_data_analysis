{{
    config(materialized='table')
}}

-- ref_demand_by_slot
-- Derives minimum staff required per 30-minute operating slot per day of week
-- from historical transaction volume.
--
-- service_rate: bills one staff member can handle per 30-min slot.
-- Override with: dbt run --select ref_demand_by_slot --vars '{"service_rate": 10}'
-- Default: {{ var('service_rate', 8) }}

WITH slot_daily AS (
    SELECT
        date,
        DAYOFWEEK(date)                                     AS day_of_week,
        MAKE_TIME(
            HOUR(transaction_time),
            CAST(FLOOR(MINUTE(transaction_time) / 30) * 30 AS BIGINT),
            0.0
        )                                                   AS slot_start,
        COUNT(DISTINCT bill_number)                         AS bills
    FROM {{ ref('fact_transactions') }}
    WHERE time_parse_failed = false
      AND (
          transaction_time >= '10:00:00'   -- operating hours: 10:00–01:00
          OR transaction_time <= '01:00:00'
      )
    GROUP BY 1, 2, 3
),

aggregated AS (
    SELECT
        day_of_week,
        slot_start,
        ROUND(AVG(bills), 1)                                                    AS avg_bills,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY bills), 1)           AS p75_bills
    FROM slot_daily
    GROUP BY 1, 2
)

SELECT
    day_of_week,
    slot_start,
    avg_bills,
    p75_bills,
    GREATEST(1, CEIL(p75_bills / {{ var('service_rate', 8) }}))                 AS min_staff
FROM aggregated
ORDER BY day_of_week, slot_start
