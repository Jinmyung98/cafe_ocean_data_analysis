-- =============================================================================
-- SQL Query Reference — Cafe Ocean Data Analysis
-- =============================================================================
-- All queries assume the Kaggle dataset has been loaded into fact_transactions.
-- Dialect: SQLite / DuckDB compatible.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- [1] dim_items
--
-- Derives the items dimension from distinct Item Desc + Category in
-- stg_transactions.
--
-- Defensive design: new menu items will appear in stg_transactions before
-- they are added to dim_ingredients or bridge_bill_of_materials (e.g. when a
-- new item is rung up on the till but the BOM has not been updated yet).
-- Any new item is automatically included in dim_items on the next dbt run.
-- Use the audit query (1c) to surface items still missing BOM coverage.
--
-- NOTE: Queries 1a and 1b are superseded by the dbt model at:
--       transform/models/marts/dim_items.sql
--       Run `dbt run --select dim_items` instead.
-- -----------------------------------------------------------------------------


-- 1c. Audit — items in dim_items with no Bill of Materials coverage.
--     These items will be excluded from stock demand calculations until
--     bridge_bill_of_materials is populated for them.
SELECT
    di.item_id,
    di.item_name,
    di.category,
    'Missing BOM' AS flag
FROM dim_items di
LEFT JOIN bridge_bill_of_materials bom ON di.item_id = bom.item_id
WHERE bom.item_id IS NULL
ORDER BY di.category, di.item_name;
