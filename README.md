# Cafe Ocean — Sales Data Analysis

A data analysis project using Cafe Ocean's point-of-sale records to support two
operational decisions: **staff scheduling** and **weekly stock purchasing**.

## Business Problems

**1. Staff scheduling optimisation**
Given a fortnightly staff budget and a minimum guaranteed hours commitment per
employee, determine how to allocate shifts so that coverage matches demand while
staying within budget.

**2. Stock purchasing optimisation**
Given stockroom capacity (including front-of-house storage), determine how much
of each ingredient to purchase each week to meet expected demand without
over-ordering.

## Dataset

**Primary data:** [Cafe Ocean Data Analysis](https://www.kaggle.com/datasets/gladinvarghese/cafeocean) published on Kaggle by
Gladin Varghese. Stored as an Excel file with 10 fields:

| Field | Description |
|---|---|
| Date | Transaction date |
| Bill Number | Unique transaction ID |
| Item Desc | Product sold |
| Time | Time of transaction |
| Quantity | Units sold |
| Rate | Unit price |
| Tax | Tax applied |
| Discount | Discount applied |
| Total | Final transaction value |
| Category | Product category |

**Supplementary data (manually constructed):**

| Data | Purpose |
|---|---|
| Stockroom capacity | Constraint for purchasing optimisation |
| Bill of materials (ingredient quantities per product) | Convert sales volume into raw material demand |
| Purchase unit sizes (e.g. 1 L milk per bottle) | Match demand to purchasable quantities |
| Staff guaranteed hours per fortnight | Constraint for scheduling optimisation |
| Minimum staffing requirement by time period | Derive from transaction frequency thresholds |
| Operating hours and shift windows | Define allocatable shift slots for the staffing model |
| Staff headcount and availability | Bound how many staff can be rostered on any given day |
| Supplier lead time per ingredient | Account for delivery lag in the stock purchasing model |
| Item cost / cost of goods sold (COGS) | Enable margin analysis and quantify waste in dollar terms |
| Reorder frequency and horizon | Define the purchasing cycle length (assumed weekly) |
| Ingredient shelf life | Prevent over-ordering perishables; key constraint for stock model |

## Deliverables

- Demand forecast by hour / day of week
- Waste monitoring: excess ingredient usage relative to transactions
- Automated staff roster generator

