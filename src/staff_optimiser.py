"""
Staffing optimiser for Cafe Ocean.
Builds and solves the ILP defined in docs/staffing_model.md.

Usage:
    python src/staff_optimiser.py

Outputs:
    outputs/schedule.csv  -- one row per (staff, date, slot) assigned
    Console: total cost, hours per staff, coverage audit
"""

import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pulp

# ---------------------------------------------------------------------------
# Constants (parameterise via CLI later)
# ---------------------------------------------------------------------------
DB_PATH    = Path(__file__).parent.parent / "data" / "cafe_ocean.duckdb"
START_DATE = datetime.date(2024, 1, 1)   # Monday
HORIZON    = 14                           # days in planning horizon
L          = 12                           # max continuous working slots (6 h)
M          = 16                           # max daily span in slots (8 h)
TAU        = 0.5                          # hours per slot

# 30 operating slots in order: 10:00, 10:30, ..., 23:30, 00:00, 00:30
SLOT_LABELS = [f"{h:02d}:{m:02d}:00" for h in range(10, 24) for m in (0, 30)]
SLOT_LABELS += ["00:00:00", "00:30:00"]
SLOT_POS = {label: i for i, label in enumerate(SLOT_LABELS)}
N_SLOTS  = len(SLOT_LABELS)   # 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _time_to_str(t) -> str:
    """Normalise a TIME value (str, datetime.time, or timedelta) to 'HH:MM:SS'."""
    if isinstance(t, str):
        return t
    if isinstance(t, datetime.time):
        return t.strftime("%H:%M:%S")
    if isinstance(t, datetime.timedelta):   # DuckDB sometimes returns TIME as timedelta
        h, rem = divmod(int(t.total_seconds()), 3600)
        mins, s = divmod(rem, 60)
        return f"{h:02d}:{mins:02d}:{s:02d}"
    return str(t)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(db_path: Path):
    con = duckdb.connect(str(db_path), read_only=True)

    demand_df = con.execute(
        "SELECT day_of_week, slot_start, min_staff FROM main_marts.ref_demand_by_slot"
    ).df()
    demand_df["slot_str"] = demand_df["slot_start"].apply(_time_to_str)

    staff_df = con.execute(
        "SELECT staff_id, hourly_rate, guaranteed_hours_per_fortnight FROM main.dim_staff"
    ).df()

    avail_df = con.execute(
        "SELECT staff_id, day_of_week, slot_start "
        "FROM main.bridge_staff_availability WHERE available = 1"
    ).df()
    avail_df["slot_str"] = avail_df["slot_start"].apply(_time_to_str)

    con.close()
    return demand_df, staff_df, avail_df


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------
def build_indices(demand_df, staff_df, avail_df):
    # demand[(day_of_week, slot_pos)] = min_staff required
    demand = {}
    for _, row in demand_df.iterrows():
        pos = SLOT_POS.get(row["slot_str"])
        if pos is not None:
            demand[(int(row["day_of_week"]), pos)] = int(row["min_staff"])

    staff_ids  = list(staff_df["staff_id"])
    wage       = dict(zip(staff_df["staff_id"], staff_df["hourly_rate"]))
    guaranteed = dict(zip(staff_df["staff_id"], staff_df["guaranteed_hours_per_fortnight"]))

    # avail_set: (staff_id, day_of_week, slot_pos) — fast membership test
    avail_set = set()
    for _, row in avail_df.iterrows():
        pos = SLOT_POS.get(row["slot_str"])
        if pos is not None:
            avail_set.add((row["staff_id"], int(row["day_of_week"]), pos))

    # calendar: list of (date_index, date, day_of_week in DAYOFWEEK convention)
    # Python weekday(): Mon=0..Sun=6 → DAYOFWEEK: Sun=0, Mon=1..Sat=6
    calendar = []
    for d in range(HORIZON):
        date = START_DATE + datetime.timedelta(days=d)
        dow  = (date.weekday() + 1) % 7
        calendar.append((d, date, dow))

    return demand, staff_ids, wage, guaranteed, avail_set, calendar


# ---------------------------------------------------------------------------
# ILP model
# ---------------------------------------------------------------------------
def build_and_solve(demand, staff_ids, wage, guaranteed, avail_set, calendar):
    prob = pulp.LpProblem("cafe_ocean_staffing", pulp.LpMinimize)

    # x[s, d, t] = 1 if staff s works slot t on day d
    x = {
        (s, d, t): pulp.LpVariable(f"x_{s}_{d}_{t}", cat="Binary")
        for s in staff_ids
        for d, _, _ in calendar
        for t in range(N_SLOTS)
    }

    # Objective: minimise total wage cost
    prob += pulp.lpSum(
        TAU * wage[s] * x[s, d, t]
        for s in staff_ids
        for d, _, _ in calendar
        for t in range(N_SLOTS)
    )

    # (1) Demand coverage
    for d, _, dow in calendar:
        for t in range(N_SLOTS):
            req = demand.get((dow, t), 0)
            if req > 0:
                prob += (
                    pulp.lpSum(x[s, d, t] for s in staff_ids) >= req,
                    f"demand_d{d}_t{t}"
                )

    # (2) Availability gate
    for s in staff_ids:
        for d, _, dow in calendar:
            for t in range(N_SLOTS):
                if (s, dow, t) not in avail_set:
                    prob += (x[s, d, t] == 0, f"avail_{s}_d{d}_t{t}")

    # (3) Break rule: in any L+1 consecutive slots, at most L may be worked
    for s in staff_ids:
        for d, _, _ in calendar:
            for t in range(N_SLOTS - L):
                prob += (
                    pulp.lpSum(x[s, d, t + i] for i in range(L + 1)) <= L,
                    f"break_{s}_d{d}_t{t}"
                )

    # (4) 8-hour span cap: any two slots >= M apart in the same day cannot both be worked
    for s in staff_ids:
        for d, _, _ in calendar:
            for p in range(N_SLOTS):
                for q in range(p + M, N_SLOTS):
                    prob += (
                        x[s, d, p] + x[s, d, q] <= 1,
                        f"span_{s}_d{d}_p{p}_q{q}"
                    )

    # (5) Guaranteed hours
    for s in staff_ids:
        prob += (
            TAU * pulp.lpSum(x[s, d, t] for d, _, _ in calendar for t in range(N_SLOTS))
            >= guaranteed[s],
            f"guar_{s}"
        )

    # gapRel=0.01: stop when best integer solution is within 1% of lower bound.
    # timeLimit=300: hard fallback in case gap closes slowly.
    solver = pulp.PULP_CBC_CMD(msg=1, gapRel=0.01, timeLimit=300)
    status = prob.solve(solver)
    return prob, x, status


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def extract_schedule(x, staff_ids, calendar):
    rows = []
    for s in staff_ids:
        for d, date, dow in calendar:
            for t in range(N_SLOTS):
                if pulp.value(x[s, d, t]) and pulp.value(x[s, d, t]) > 0.5:
                    rows.append({
                        "staff_id":   s,
                        "date":       date,
                        "day_of_week": dow,
                        "slot_pos":   t,
                        "slot_start": SLOT_LABELS[t],
                    })
    return pd.DataFrame(rows)


def print_summary(schedule_df, staff_ids, wage, guaranteed):
    print("\n=== Schedule Summary ===")
    total_cost = 0.0
    for s in staff_ids:
        hours = len(schedule_df[schedule_df["staff_id"] == s]) * TAU
        cost  = hours * wage[s]
        total_cost += cost
        flag = "  !! BELOW GUARANTEE" if hours < guaranteed[s] - 0.01 else ""
        print(f"  {s}  {hours:5.1f} h  (min {guaranteed[s]} h)  ${cost:8.2f}{flag}")
    print(f"\n  TOTAL COST : ${total_cost:,.2f}  (minimum cost to meet coverage + guaranteed hours)")


def check_coverage(schedule_df, demand, calendar):
    violations = []
    for d, date, dow in calendar:
        day_sched = schedule_df[schedule_df["date"] == date]
        for t in range(N_SLOTS):
            req      = demand.get((dow, t), 0)
            assigned = len(day_sched[day_sched["slot_pos"] == t])
            if assigned < req:
                violations.append((date, SLOT_LABELS[t], req, assigned))
    if violations:
        print(f"\n  Coverage violations: {len(violations)}")
        for v in violations[:10]:
            print(f"    {v[0]} {v[1]}: need {v[2]}, got {v[3]}")
    else:
        print("\n  Coverage: OK — all demand slots met")


# ---------------------------------------------------------------------------
# Infeasibility diagnostics
# ---------------------------------------------------------------------------
DOW_NAME = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def check_availability_gaps(demand, staff_ids, avail_set, calendar):
    """Tier 1: slots where raw available-staff count is below demand (hard infeasibilities)."""
    gaps = []
    seen = set()
    for _, _, dow in calendar:
        if dow in seen:
            continue
        seen.add(dow)
        for t in range(N_SLOTS):
            req = demand.get((dow, t), 0)
            if req == 0:
                continue
            n_avail = sum(1 for s in staff_ids if (s, dow, t) in avail_set)
            if n_avail < req:
                gaps.append((DOW_NAME[dow], SLOT_LABELS[t], req, n_avail, req - n_avail))

    if gaps:
        print(f"\n[WARNING] Tier 1 — {len(gaps)} slot(s) where demand exceeds available staff:")
        for name, slot, req, avail, gap in gaps:
            print(f"  {name} {slot}: need {req}, available {avail}  (+{gap} gap)")
        print("  Fix: extend availability on these slots or hire additional staff.")
    else:
        print("  Tier 1 availability check: OK")
    return gaps


def solve_relaxed(demand, staff_ids, avail_set, calendar):
    """Tier 2: relax coverage to soft constraints; minimise total unmet demand."""
    prob = pulp.LpProblem("cafe_ocean_staffing_relaxed", pulp.LpMinimize)

    x = {
        (s, d, t): pulp.LpVariable(f"xr_{s}_{d}_{t}", cat="Binary")
        for s in staff_ids
        for d, _, _ in calendar
        for t in range(N_SLOTS)
    }
    slack = {
        (d, t): pulp.LpVariable(f"sl_{d}_{t}", lowBound=0)
        for d, _, dow in calendar
        for t in range(N_SLOTS)
        if demand.get((dow, t), 0) > 0
    }

    prob += pulp.lpSum(slack.values())

    # Coverage with slack (soft)
    for d, _, dow in calendar:
        for t in range(N_SLOTS):
            req = demand.get((dow, t), 0)
            if req > 0:
                prob += (
                    pulp.lpSum(x[s, d, t] for s in staff_ids) + slack[d, t] >= req,
                    f"cov_r_d{d}_t{t}"
                )

    # Availability (hard)
    for s in staff_ids:
        for d, _, dow in calendar:
            for t in range(N_SLOTS):
                if (s, dow, t) not in avail_set:
                    prob += (x[s, d, t] == 0, f"avail_r_{s}_d{d}_t{t}")

    # Break rule (hard)
    for s in staff_ids:
        for d, _, _ in calendar:
            for t in range(N_SLOTS - L):
                prob += (
                    pulp.lpSum(x[s, d, t + i] for i in range(L + 1)) <= L,
                    f"break_r_{s}_d{d}_t{t}"
                )

    # 8-hour span cap (hard)
    for s in staff_ids:
        for d, _, _ in calendar:
            for p in range(N_SLOTS):
                for q in range(p + M, N_SLOTS):
                    prob += (
                        x[s, d, p] + x[s, d, q] <= 1,
                        f"span_r_{s}_d{d}_p{p}_q{q}"
                    )

    solver = pulp.PULP_CBC_CMD(msg=0)
    status = prob.solve(solver)
    return slack, status


def print_relaxed_report(slack, demand, calendar):
    shortfalls = []
    for d, date, dow in calendar:
        for t in range(N_SLOTS):
            req = demand.get((dow, t), 0)
            if req > 0 and (d, t) in slack:
                val = pulp.value(slack[d, t])
                if val and val > 0.01:
                    shortfalls.append({
                        "date": date, "dow": dow,
                        "slot": SLOT_LABELS[t],
                        "required": int(req),
                        "shortfall": round(val, 1),
                    })

    if not shortfalls:
        print("\n  Tier 2: all slots are coverable with hard constraints alone.")
        print("  Infeasibility is driven by budget or guaranteed-hours constraints, not capacity.")
        print("  Try raising BUDGET or lowering guaranteed_hours_per_fortnight for some staff.")
    else:
        df = pd.DataFrame(shortfalls)
        print(f"\n  Tier 2 — {len(shortfalls)} slot(s) cannot be fully staffed even at max capacity:")
        for date in sorted(df["date"].unique()):
            rows = df[df["date"] == date]
            print(f"\n  {date} ({DOW_NAME[rows.iloc[0]['dow']]}):")
            for _, r in rows.iterrows():
                print(f"    {r['slot']}: need {r['required']}, short by {r['shortfall']:.1f}")
        print(f"\n  Total unmet staff-slots over the fortnight: {df['shortfall'].sum():.1f}")
        print("  Fix: extend availability or hire for the slots listed above.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading data from DuckDB...")
    demand_df, staff_df, avail_df = load_data(DB_PATH)

    print("Building indices...")
    demand, staff_ids, wage, guaranteed, avail_set, calendar = build_indices(
        demand_df, staff_df, avail_df
    )
    print(f"  {len(staff_ids)} staff  |  {HORIZON} days  |  {N_SLOTS} slots/day")
    print(f"  {len(demand)} demand entries  |  {len(avail_set)} availability entries")

    # Tier 1: pre-solve availability gap check
    check_availability_gaps(demand, staff_ids, avail_set, calendar)

    print("\nBuilding and solving ILP...")
    prob, x, status = build_and_solve(demand, staff_ids, wage, guaranteed, avail_set, calendar)

    status_str = pulp.LpStatus[status]
    print(f"\nSolver status: {status_str}")

    if status_str != "Optimal":
        print("No optimal solution found.")
        answer = input("\nRun relaxed model to identify coverage shortfalls? [y/N] ").strip().lower()
        if answer == "y":
            print("Solving relaxed model (minimising unmet demand)...")
            slack, r_status = solve_relaxed(demand, staff_ids, avail_set, calendar)
            if pulp.LpStatus[r_status] == "Optimal":
                print_relaxed_report(slack, demand, calendar)
            else:
                print("Relaxed model also infeasible — check hard constraints (availability, break rules).")
        return

    schedule_df = extract_schedule(x, staff_ids, calendar)

    out = Path(__file__).parent.parent / "outputs" / "schedule.csv"
    schedule_df.to_csv(out, index=False)
    print(f"Schedule saved to {out}  ({len(schedule_df)} rows)")

    print_summary(schedule_df, staff_ids, wage, guaranteed)
    check_coverage(schedule_df, demand, calendar)


if __name__ == "__main__":
    main()
