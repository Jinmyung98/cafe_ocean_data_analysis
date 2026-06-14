"""
Stock purchasing optimiser for Cafe Ocean.
Builds and solves the ILP defined in docs/stock_model.md.

Usage:
    python src/stock_optimiser.py

Outputs:
    outputs/purchase_order.csv  -- one row per ingredient with order quantity and cost
    Console: pre-checks, order summary, total cost
"""

import math
from pathlib import Path

import duckdb
import pandas as pd
import pulp

# ---------------------------------------------------------------------------
# Constants (parameterise via CLI later)
# ---------------------------------------------------------------------------
DB_PATH       = Path(__file__).parent.parent / "data" / "cafe_ocean.duckdb"
SAFETY_STOCK  = 0.20   # 20% buffer above p75 demand
CURRENT_STOCK = {}     # k_i: defaults to 0 for all ingredients (demo)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(db_path: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute("""
        SELECT
            r.ingredient_id,
            r.ingredient_name,
            r.unit,
            r.shelf_life_days,
            r.purchase_unit_size,
            r.purchase_unit_cost,
            r.supplier_id,
            r.p75_weekly_units,
            r.weeks_observed,
            COALESCE(s.max_quantity, 1e9) AS max_quantity
        FROM main_marts.ref_ingredient_demand_weekly r
        LEFT JOIN main.ref_stockroom_capacity        s
               ON r.ingredient_id = s.ingredient_id
        ORDER BY r.ingredient_id
    """).df()
    con.close()
    return df


# ---------------------------------------------------------------------------
# Pre-checks (before building the ILP)
# ---------------------------------------------------------------------------
def pre_check(df: pd.DataFrame, sigma: float) -> tuple[pd.DataFrame, list[str]]:
    """
    Flag ingredients where shelf life conflicts with coverage or capacity
    is too small to meet demand.  Returns the feasible subset and a list
    of warning messages.
    """
    warnings = []
    feasible_mask = pd.Series(True, index=df.index)

    for _, row in df.iterrows():
        d   = row["p75_weekly_units"]
        k   = CURRENT_STOCK.get(row["ingredient_id"], 0)
        cap = row["max_quantity"]
        tau = row["shelf_life_days"]
        req = math.ceil((1 + sigma) * d)          # units needed after delivery

        shelf_max = math.floor((tau / 7) * d)     # max holdable within shelf life

        if shelf_max < req - k:
            warnings.append(
                f"  SHELF LIFE  {row['ingredient_id']} {row['ingredient_name']}: "
                f"shelf life {tau}d → can hold {shelf_max} {row['unit']} "
                f"but need {req - k} — order more frequently."
            )
            feasible_mask[_] = False
            continue

        if cap < req - k:
            warnings.append(
                f"  CAPACITY    {row['ingredient_id']} {row['ingredient_name']}: "
                f"storage cap {cap} {row['unit']} < required {req - k} — expand storage."
            )
            feasible_mask[_] = False

    return df[feasible_mask].copy(), warnings


# ---------------------------------------------------------------------------
# ILP model
# ---------------------------------------------------------------------------
def build_and_solve(df: pd.DataFrame, sigma: float):
    prob = pulp.LpProblem("cafe_ocean_stock", pulp.LpMinimize)

    n = {
        row["ingredient_id"]: pulp.LpVariable(
            f"n_{row['ingredient_id']}", lowBound=0, cat="Integer"
        )
        for _, row in df.iterrows()
    }

    # Objective: minimise total purchase cost
    prob += pulp.lpSum(
        n[row["ingredient_id"]] * row["purchase_unit_cost"]
        for _, row in df.iterrows()
    )

    for _, row in df.iterrows():
        i   = row["ingredient_id"]
        d   = row["p75_weekly_units"]
        k   = CURRENT_STOCK.get(i, 0)
        u   = row["purchase_unit_size"]
        cap = row["max_quantity"]
        tau = row["shelf_life_days"]

        req       = math.ceil((1 + sigma) * d)
        shelf_max = math.floor((tau / 7) * d)

        # (1) Demand coverage
        prob += (k + n[i] * u >= req,           f"cov_{i}")
        # (2) Storage capacity
        prob += (k + n[i] * u <= cap,           f"cap_{i}")
        # (3) Shelf life cap
        prob += (k + n[i] * u <= shelf_max,     f"shelf_{i}")

    solver = pulp.PULP_CBC_CMD(msg=0)
    status = prob.solve(solver)
    return prob, n, status


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def build_order(df: pd.DataFrame, n: dict) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        i        = row["ingredient_id"]
        units    = int(round(pulp.value(n[i]) or 0))
        qty      = units * row["purchase_unit_size"]
        cost     = units * row["purchase_unit_cost"]
        k        = CURRENT_STOCK.get(i, 0)
        rows.append({
            "ingredient_id":    i,
            "ingredient_name":  row["ingredient_name"],
            "supplier_id":      row["supplier_id"],
            "unit":             row["unit"],
            "p75_weekly_demand":row["p75_weekly_units"],
            "current_stock":    k,
            "purchase_units":   units,
            "total_qty_ordered":qty,
            "stock_after_delivery": k + qty,
            "purchase_cost":    cost,
        })
    return pd.DataFrame(rows)


def print_order(order_df: pd.DataFrame):
    print("\n=== Weekly Purchase Order ===")
    print(f"{'Ingredient':<30} {'Demand':>8} {'Ord.Units':>9} {'Qty':>8} {'Cost (₹)':>10}")
    print("-" * 68)
    for _, r in order_df.iterrows():
        if r["purchase_units"] > 0:
            print(
                f"{r['ingredient_name']:<30} "
                f"{r['p75_weekly_demand']:>8.0f} "
                f"{r['purchase_units']:>9} "
                f"{r['total_qty_ordered']:>8.0f} "
                f"{r['purchase_cost']:>10,.0f}"
            )
    total = order_df["purchase_cost"].sum()
    items = (order_df["purchase_units"] > 0).sum()
    print("-" * 68)
    print(f"{'TOTAL':>58} {total:>10,.0f}")
    print(f"\n  {items} ingredient lines  |  total ₹{total:,.0f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading demand and capacity data...")
    df = load_data(DB_PATH)
    print(f"  {len(df)} ingredients with weekly demand estimates")

    print(f"\nPre-checking feasibility (safety stock σ = {SAFETY_STOCK:.0%})...")
    feasible_df, warnings = pre_check(df, SAFETY_STOCK)
    if warnings:
        print(f"  {len(warnings)} issue(s) flagged — excluded from this order:")
        for w in warnings:
            print(w)
    else:
        print("  All ingredients feasible")

    if feasible_df.empty:
        print("No feasible ingredients to order.")
        return

    print(f"\nBuilding and solving ILP ({len(feasible_df)} ingredients)...")
    prob, n, status = build_and_solve(feasible_df, SAFETY_STOCK)
    status_str = pulp.LpStatus[status]
    print(f"  Solver status: {status_str}")

    if status_str != "Optimal":
        print("No optimal solution found.")
        return

    order_df = build_order(feasible_df, n)

    out = Path(__file__).parent.parent / "outputs" / "purchase_order.csv"
    order_df.to_csv(out, index=False)
    print(f"  Order saved to {out}")

    print_order(order_df)


if __name__ == "__main__":
    main()
