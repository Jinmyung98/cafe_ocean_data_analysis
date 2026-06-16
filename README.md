# Cafe Ocean — Operations Analytics

Turn a café-bar's point-of-sale history into two operational decisions —
**staff scheduling** and **weekly stock purchasing** — through an analytics-engineering
pipeline (DuckDB + dbt) and integer-programming optimisers (PuLP + CBC).

---

## Business problems

**1. Staff scheduling.** Given each employee's availability, approved leave, guaranteed
hours and an overtime cap, decide who works which 30-minute slots over a fortnight so
that customer demand is covered at **minimum wage cost**, while respecting labour rules:
max 6 h continuous before a break, a 30-minute break, max 8 h daily span, a single
contiguous shift per day, and a 3 h minimum shift.

**2. Stock purchasing.** Given each ingredient's weekly demand (derived from sales × a
bill of materials), storage capacity and shelf life, decide how many purchase units to
order each week at **minimum cost** without over-ordering perishables.

---

## Approach

```
Kaggle Excel ─► load_raw.py ─► DuckDB (raw_transactions)
                                  └─ dbt staging  stg_transactions  (typed, cleaned)
                                      └─ dbt marts dim_items, fact_transactions,
                                                   ref_demand_by_slot,
                                                   ref_ingredient_demand_weekly
Manually-maintained reference data ─► dbt seeds  (staff, availability, leave, BOM,
                                                  ingredients, suppliers, capacity, promos)

Optimisers (PuLP + CBC):
  src/staff_optimiser.py   demand + availability + leave + labour rules ─► roster
  src/stock_optimiser.py   weekly demand + capacity + shelf life         ─► purchase order
  src/roster_sheet.py      per-slot schedule ─► distributable weekly roster
```

Full schema in [docs/data_model.md](docs/data_model.md); ILP formulations in
[docs/staffing_model.md](docs/staffing_model.md) and
[docs/stock_model.md](docs/stock_model.md).

**Tech stack:** Python 3.12 · DuckDB · dbt (dbt-duckdb) · PuLP + CBC · pandas · Jupyter · matplotlib / seaborn · Plotly Dash

---

## Dataset

[Cafe Ocean dataset](https://www.kaggle.com/datasets/gladinvarghese/cafeocean) (Kaggle,
Gladin Varghese) — ~145,800 transaction line items in an Excel file:

| Field | Description |
|---|---|
| Date | Transaction date |
| Bill Number | Transaction ID |
| Item Desc | Product sold |
| Time | Time of transaction |
| Quantity | Units sold |
| Rate | Unit price |
| Tax | Tax applied |
| Discount | Discount applied |
| Total | Final transaction value |
| Category | Product category |

All supplementary tables (staff, availability, leave, ingredients, suppliers, bill of
materials, stockroom capacity, promotions) are **manually-maintained dbt seeds** with
illustrative demo values.

---

## Key results (demo run)

**Staffing** — 12 staff, one fortnight, 30-minute slots:
- Minimum-cost roster ≈ **$8,693 / 533.5 h**, all demand covered, every labour rule and
  leave request honoured, in a single contiguous shift per person per day.
- Surfaces real trade-offs: capping individual overtime at *guaranteed + 16 h* forced a
  full-time hire, and the solution runs at **~98% of capped capacity** — a fragility flag.

**Stock** — 26 ingredients, one week:
- Minimum-cost order ≈ **₹122,365**; the hookah/tobacco line (the #1 revenue category in
  the EDA) dominates spend.
- Pre-checks flag **3 perishables** (milk, bread, deli meat) that cannot be bulk-ordered
  weekly — they belong on a shorter delivery cycle, which the model reports rather than
  silently mis-ordering.

---

## Run it

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; use source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

python src/load_raw.py --full-refresh          # Excel -> DuckDB raw_transactions
cd transform && dbt seed && dbt run && cd ..    # seeds + staging + marts

python src/staff_optimiser.py     # -> outputs/schedule.csv
python src/roster_sheet.py        # -> outputs/roster.md, outputs/roster.csv
python src/stock_optimiser.py     # -> outputs/purchase_order.csv
```

Place the Kaggle Excel at `data/raw/Cafe_Ocean.xlsx` first (the raw file is gitignored).

---

## Outputs

| File | Contents |
|---|---|
| `outputs/schedule.csv` | Per-slot staff assignments |
| `outputs/roster.md` / `roster.csv` | Distributable fortnight roster (weekly grids, OFF/LEAVE) |
| `outputs/purchase_order.csv` | Weekly ingredient purchase order |
| `outputs/figures/` | EDA charts |

---

## Dashboard

An interactive [Plotly Dash](https://dash.plotly.com/) app (`app/`) presents the pipeline
and optimiser outputs across three pages, styled as a report (serif display headings,
KPI cards, mono section labels, a dark hero header and footer). Each page closes with a
"takeaways" row of insight cards summarising its headline numbers.

- **Operations** — revenue / bills / average-bill KPIs with date and category filters;
  a revenue-by-category donut, top items, a day-of-week × hour demand heatmap, daily trend
  and hourly profile (from `fact_transactions` × `dim_items`).
- **Roster** — a fortnight shift Gantt (one row per staff, coloured by role, leave shaded),
  hours against each person's guaranteed floor and overtime cap, and a coverage-vs-demand
  check for any chosen day (from `outputs/schedule.csv` + staff seeds + `ref_demand_by_slot`).
- **Stock** — order cost by supplier and menu category, storage utilisation after delivery,
  weekly demand per ingredient, and the perishables the optimiser excludes
  (from `outputs/purchase_order.csv` + demand, capacity and supplier tables).

```bash
pip install -r requirements.txt   # includes dash + plotly
python app/app.py                 # serves at http://127.0.0.1:8050
```

It reads `data/cafe_ocean.duckdb` read-only. The Roster and Stock pages need the
optimisers to have been run first; until their output CSVs exist, each page shows a
prompt to run the relevant script rather than erroring.

---

## Assumptions & limitations

- **Demand is predicted, not causal** — derived from historical averages; assumes the
  future resembles the past, with no allowance for one-off events or trend growth.
- **Service-rate sensitivity** — staffing demand depends on an assumed bills-per-staff
  service rate (a dbt variable); results should be read as sensitivity to that assumption.
- **Solver gap** — the single-shift staffing ILP has a weak LP relaxation, so it is solved
  to a 5% optimality gap (~2–3 min) rather than proven optimal.
- **Illustrative seeds** — all manually-seeded values (wages, ingredient prices, BOM,
  capacities, leave) are demo data, not Cafe Ocean's real figures.

---

## Roadmap

- **Dashboards** ✓ — café-operations, roster, and stock pages shipped (see
  [Dashboard](#dashboard)).
- **Live data refresh** — schedule the raw load + dbt build so the marts and dashboard
  track new sales automatically.
