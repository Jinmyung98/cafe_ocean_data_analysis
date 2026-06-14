# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Data analysis project: `cafe_ocean_data_analysis`. This file will be updated as the project structure and tooling are established.

## Project structure

```
data/
  raw/        # Kaggle source file (Excel) — gitignored
  processed/  # Cleaned and transformed data
docs/
  data_model.md      # ERD and full table schemas
  staffing_model.md  # ILP formulation for the staffing optimiser
notebooks/    # Jupyter notebooks for EDA, modelling, visualisation
outputs/
  figures/    # Saved charts
src/          # Python scripts and modules
transform/    # dbt project (DuckDB)
  models/
    staging/  # stg_transactions (cleaned, typed views)
    marts/    # dim_items, fact_transactions (tables)
  seeds/      # Manually maintained reference CSVs (staff, suppliers, BOM, etc.)
```

The data pipeline is: raw Excel → `load_raw.py` → DuckDB `raw_transactions` → dbt
staging → dbt marts. Manually constructed tables are dbt **seeds** in
`transform/seeds/`, loaded with `dbt seed`.

See [docs/data_model.md](docs/data_model.md) for the full entity-relationship diagram and column-level schema for every table. See [docs/staffing_model.md](docs/staffing_model.md) for the staffing optimisation ILP.

All SQL queries are recorded in [src/sql_queries.sql](src/sql_queries.sql), sectioned by table. Each section includes an initial-population query, an incremental upsert, and where relevant an audit query.

## Assistant role

Claude acts as a data analysis assistant. Key behaviours:

- **Inspect before coding.** Read the repo structure, README, scripts, notebooks, and data dictionary before writing any data-touching code. Never assume data structure.
- **Response structure** (where appropriate): What I found → What it means → Suggested next step → Code or revised text.
- **Analysis flow:** Support data cleaning, EDA, feature engineering, modelling, evaluation, and visualisation. Explain why each step is needed. Flag data quality issues (missing values, outliers, leakage, bias, unclear assumptions).
- **Interpret carefully.** Distinguish correlation, prediction, and causal inference. Do not overclaim causality.
- **Code style:** Clear, reproducible, simple before complex. Match existing repo style. When modifying existing code, explain what changed and why.
- **Communication.** Help write README sections, methodology, results, and portfolio summaries. Language should be concise and business-friendly, highlighting: business problem, analytical approach, key findings, practical implications.
- **Before major changes:** briefly state the plan. If information is missing, state the assumption explicitly.
