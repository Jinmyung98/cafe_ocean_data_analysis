# Stock Purchasing Optimisation Model

## Overview

The stock model decides **how many purchase units of each ingredient to order each week**
so that forecasted demand is covered (with a safety buffer) without exceeding storage
capacity or shelf life, at minimum purchase cost.

It is formulated as an **Integer Linear Programme (ILP)** and solved with PuLP + CBC.

---

## Assumptions

1. A single weekly order is placed for all ingredients from their assigned supplier.
2. Demand is estimated from historical weekly ingredient consumption (p75 from
   `ref_ingredient_demand_weekly`), applying `stock_multiplier` from `ref_promotional_items`
   to account for 1+1 / 2+1 / BUNDLE promotions that dispense more stock than billed.
3. A fixed **safety stock factor** σ (default 20%) is added above the p75 demand to buffer
   against demand spikes.
4. Current on-hand stock `k_i` is provided as a model input (default: 0 — ordering from
   empty stock for demo purposes).
5. Orders must be placed in whole purchase units (e.g., whole pouches, whole cases).
6. Ingredients with shelf life shorter than 7 days cannot accumulate a full week's stock
   in one order. The model flags these as requiring more frequent ordering and excludes
   them from the single-order optimisation.
7. Storage capacity is enforced per ingredient from `ref_stockroom_capacity`.

---

## Sets and Parameters

| Symbol | Meaning | Source |
|---|---|---|
| $i \in I$ | Ingredients with BOM coverage | `dim_ingredients` ∩ `bridge_bill_of_materials` |
| $d_i$ | p75 weekly demand (ingredient units) | `ref_ingredient_demand_weekly` |
| $k_i$ | Current on-hand stock (ingredient units) | model input |
| $\text{cap}_i$ | Maximum storage quantity (ingredient units) | `ref_stockroom_capacity` |
| $u_i$ | Purchase unit size (ingredient units per unit ordered) | `dim_ingredients` |
| $c_i$ | Purchase unit cost (₹) | `dim_ingredients` |
| $\tau_i$ | Shelf life (days) | `dim_ingredients` |
| $\sigma$ | Safety stock factor (default 0.20) | model input |

---

## Decision Variable

$$
n_i \in \mathbb{Z}_{\geq 0} \quad \text{purchase units to order for ingredient } i
$$

---

## Objective

Minimise total purchase cost for the weekly order:

$$
\min \; \sum_{i \in I} n_i \cdot c_i
$$

---

## Constraints

**(1) Demand coverage with safety stock:**

$$
k_i + n_i \cdot u_i \;\geq\; \lceil (1 + \sigma) \cdot d_i \rceil
\qquad \forall i \in I
$$

**(2) Storage capacity:**

$$
k_i + n_i \cdot u_i \;\leq\; \text{cap}_i
\qquad \forall i \in I
$$

**(3) Shelf life cap** — do not order more than can be consumed before expiry:

$$
k_i + n_i \cdot u_i \;\leq\; \left\lfloor \frac{\tau_i}{7} \cdot d_i \right\rfloor
\qquad \forall i \in I
$$

**(4) Non-negative integer:**

$$
n_i \in \mathbb{Z}_{\geq 0}
\qquad \forall i \in I
$$

---

## Feasibility and Diagnostics

Constraint (1) and (3) conflict when the shelf life is shorter than the safety stock
requirement: $\frac{\tau_i}{7} < (1 + \sigma)$. This means one weekly order cannot hold
enough stock to cover the week. Before solving the ILP, the model pre-checks each
ingredient and reports:

- **Shelf life conflict** — ingredient needs ordering more than once per week.
  Resolution: increase order frequency or negotiate a longer shelf life with the supplier.
- **Capacity conflict** — storage cannot hold enough stock to meet demand.
  Resolution: increase storage capacity or reduce safety stock factor.

---

## Notes and Limitations

- **Single period.** The model optimises one weekly order. It does not plan across
  multiple weeks and does not account for lead time within the week (Assumption 1).
- **No supplier selection.** Each ingredient is sourced from one fixed supplier. Adding
  alternative suppliers would introduce binary selection variables.
- **Demand is predicted, not causal.** p75 weekly demand is derived from historical
  averages. It does not account for seasonal trends, planned events, or promotions not
  yet reflected in the data.
- **Current stock is a model input.** In production, `k_i` should be read from a live
  inventory system. For demo purposes all `k_i = 0`.
- **Budget extension.** A fortnightly spend cap can be added as a fifth constraint:
  $\sum_i n_i \cdot c_i \leq B$. This couples ingredients and turns the problem into a
  bounded knapsack, requiring prioritisation by revenue contribution.
