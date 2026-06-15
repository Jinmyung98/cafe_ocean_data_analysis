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
MAX_EXTRA_HOURS = 16                      # fortnightly hours cap = guaranteed + this
MIN_SHIFT_SLOTS = 6                       # minimum shift length when working (6 slots = 3 h)
GAP_REL    = 0.05                         # accept a solution within 5% of the lower bound
TIME_LIMIT = 300                          # solver wall-clock cap (seconds)

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


def _to_date(v) -> datetime.date:
    """Normalise a DATE value (str, date, or pandas Timestamp) to datetime.date."""
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if hasattr(v, "date"):                  # pandas Timestamp
        return v.date()
    return datetime.date.fromisoformat(str(v)[:10])


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

    leave_df = con.execute(
        "SELECT staff_id, leave_date FROM main.bridge_staff_leave"
    ).df()

    con.close()
    return demand_df, staff_df, avail_df, leave_df


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------
def build_indices(demand_df, staff_df, avail_df, leave_df):
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

    # leave_set: (staff_id, date) for full-day approved leave
    leave_set = set()
    for _, row in leave_df.iterrows():
        leave_set.add((row["staff_id"], _to_date(row["leave_date"])))

    # calendar: list of (date_index, date, day_of_week in DAYOFWEEK convention)
    # Python weekday(): Mon=0..Sun=6 → DAYOFWEEK: Sun=0, Mon=1..Sat=6
    calendar = []
    for d in range(HORIZON):
        date = START_DATE + datetime.timedelta(days=d)
        dow  = (date.weekday() + 1) % 7
        calendar.append((d, date, dow))
    horizon_dates = {date for _, date, _ in calendar}

    # Day-of-week each staff is normally available for
    avail_dows = {s: set() for s in staff_ids}
    for (s, dow, _t) in avail_set:
        if s in avail_dows:
            avail_dows[s].add(dow)

    # Pro-rate guaranteed hours for approved leave: a staff member on leave is not
    # expected to make up lost hours. effective = guaranteed x (worked days / avail days).
    eff_guarantee = {}
    leave_days    = {}
    for s in staff_ids:
        avail_dates = [date for _, date, dow in calendar if dow in avail_dows[s]]
        on_leave    = [date for date in avail_dates if (s, date) in leave_set]
        leave_days[s] = len({date for (ss, date) in leave_set
                             if ss == s and date in horizon_dates})
        if avail_dates:
            frac = (len(avail_dates) - len(on_leave)) / len(avail_dates)
            eff_guarantee[s] = round(guaranteed[s] * frac, 1)
        else:
            eff_guarantee[s] = 0.0

    return (demand, staff_ids, wage, guaranteed, eff_guarantee,
            avail_set, leave_set, leave_days, calendar)


# ---------------------------------------------------------------------------
# ILP model
# ---------------------------------------------------------------------------
def build_and_solve(demand, staff_ids, wage, guaranteed, eff_guarantee,
                    avail_set, leave_set, calendar):
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

    # (2) Availability gate (also blocks approved leave dates)
    for s in staff_ids:
        for d, date, dow in calendar:
            for t in range(N_SLOTS):
                if (s, dow, t) not in avail_set or (s, date) in leave_set:
                    prob += (x[s, d, t] == 0, f"avail_{s}_d{d}_t{t}")

    # (3) Break rule: in any L+1 consecutive slots, at most L may be worked
    for s in staff_ids:
        for d, _, _ in calendar:
            for t in range(N_SLOTS - L):
                prob += (
                    pulp.lpSum(x[s, d, t + i] for i in range(L + 1)) <= L,
                    f"break_{s}_d{d}_t{t}"
                )

    # (4) 8-hour span cap: any two slots >= M apart in the same day cannot both be worked.
    #     (Pairwise form; an O(n) started/ending form was tried but slowed CBC's
    #     feasibility search on this model, so the pairwise version is retained.)
    for s in staff_ids:
        for d, _, _ in calendar:
            for p in range(N_SLOTS):
                for q in range(p + M, N_SLOTS):
                    prob += (
                        x[s, d, p] + x[s, d, q] <= 1,
                        f"span_{s}_d{d}_p{p}_q{q}"
                    )

    # (5) Guaranteed hours (pro-rated for approved leave)
    for s in staff_ids:
        prob += (
            TAU * pulp.lpSum(x[s, d, t] for d, _, _ in calendar for t in range(N_SLOTS))
            >= eff_guarantee[s],
            f"guar_{s}"
        )

    # (6) Maximum hours: at most guaranteed + MAX_EXTRA_HOURS over the fortnight
    for s in staff_ids:
        prob += (
            TAU * pulp.lpSum(x[s, d, t] for d, _, _ in calendar for t in range(N_SLOTS))
            <= guaranteed[s] + MAX_EXTRA_HOURS,
            f"maxhours_{s}"
        )

    # (7) Single contiguous shift per day with at most one 30-min break, and
    # (8) a minimum shift length on any day a staff member works.
    #
    # started[t] = 1 once the shift has begun (prefix-OR of x); ending[t] = 1 while
    # still on shift (suffix-OR of x). An unworked slot with work on BOTH sides is an
    # interior break; allowing at most one such slot forces a single contiguous shift
    # broken by at most one 30-minute break.
    for s in staff_ids:
        for d, _, _ in calendar:
            started = {t: pulp.LpVariable(f"st_{s}_{d}_{t}", lowBound=0, upBound=1)
                       for t in range(N_SLOTS)}
            ending  = {t: pulp.LpVariable(f"en_{s}_{d}_{t}", lowBound=0, upBound=1)
                       for t in range(N_SLOTS)}
            workday = pulp.LpVariable(f"wd_{s}_{d}", cat="Binary")

            for t in range(N_SLOTS):
                prob += started[t] >= x[s, d, t]
                prob += ending[t]  >= x[s, d, t]
                if t > 0:
                    prob += started[t] >= started[t - 1]
                    prob += started[t] <= started[t - 1] + x[s, d, t]
                else:
                    prob += started[t] <= x[s, d, t]
                if t < N_SLOTS - 1:
                    prob += ending[t] >= ending[t + 1]
                    prob += ending[t] <= ending[t + 1] + x[s, d, t]
                else:
                    prob += ending[t] <= x[s, d, t]

            # at most one interior break slot
            breaks = []
            for t in range(1, N_SLOTS - 1):
                b = pulp.LpVariable(f"bk_{s}_{d}_{t}", lowBound=0, upBound=1)
                prob += b >= started[t - 1] + ending[t + 1] - x[s, d, t] - 1
                breaks.append(b)
            prob += (pulp.lpSum(breaks) <= 1, f"onebreak_{s}_{d}")

            # minimum shift length, gated by whether the staff works that day
            day_slots = pulp.lpSum(x[s, d, t] for t in range(N_SLOTS))
            prob += (day_slots >= MIN_SHIFT_SLOTS * workday, f"minshift_{s}_{d}")
            for t in range(N_SLOTS):
                prob += x[s, d, t] <= workday

    # The single-shift formulation has a weak LP relaxation, so proving exact
    # optimality is slow. We accept a solution within GAP_REL of the bound, cap the
    # wall-clock at TIME_LIMIT, and use multiple threads.
    solver = pulp.PULP_CBC_CMD(msg=1, gapRel=GAP_REL, timeLimit=TIME_LIMIT, threads=4)
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


def print_summary(schedule_df, staff_ids, wage, guaranteed, eff_guarantee, leave_days):
    print("\n=== Schedule Summary ===")
    total_cost = 0.0
    for s in staff_ids:
        hours = len(schedule_df[schedule_df["staff_id"] == s]) * TAU
        cost  = hours * wage[s]
        total_cost += cost
        cap   = guaranteed[s] + MAX_EXTRA_HOURS
        floor = eff_guarantee[s]
        flag = ""
        if hours < floor - 0.01:
            flag = "  !! BELOW GUARANTEE"
        elif hours > cap + 0.01:
            flag = "  !! ABOVE CAP"
        lv = leave_days.get(s, 0)
        lv_str = f"  leave {lv}d" if lv else ""
        print(f"  {s}  {hours:5.1f} h  (min {floor:.0f}, max {cap:.0f} h)  ${cost:8.2f}{lv_str}{flag}")
    print(f"\n  TOTAL COST : ${total_cost:,.2f}  (best-found wage cost within solver gap)")


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


def check_availability_gaps(demand, staff_ids, avail_set, leave_set, calendar):
    """Tier 1: per-date slots where available staff (after leave) fall below demand."""
    gaps = []
    for d, date, dow in calendar:
        for t in range(N_SLOTS):
            req = demand.get((dow, t), 0)
            if req == 0:
                continue
            n_avail = sum(
                1 for s in staff_ids
                if (s, dow, t) in avail_set and (s, date) not in leave_set
            )
            if n_avail < req:
                gaps.append((date, SLOT_LABELS[t], req, n_avail, req - n_avail))

    if gaps:
        print(f"\n[WARNING] Tier 1 — {len(gaps)} slot(s) where demand exceeds available staff:")
        for date, slot, req, avail, gap in gaps[:15]:
            print(f"  {date} {slot}: need {req}, available {avail}  (+{gap} gap)")
        if len(gaps) > 15:
            print(f"  ... and {len(gaps) - 15} more")
        print("  Fix: extend availability, reduce overlapping leave, or add staff.")
    else:
        print("  Tier 1 availability check: OK")
    return gaps


def solve_relaxed(demand, staff_ids, avail_set, leave_set, calendar):
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

    # Availability (hard; also blocks leave)
    for s in staff_ids:
        for d, date, dow in calendar:
            for t in range(N_SLOTS):
                if (s, dow, t) not in avail_set or (s, date) in leave_set:
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
        print("  Infeasibility is driven by the guaranteed-hours or max-hours constraints, not capacity.")
        print("  Try adjusting guaranteed hours, the overtime cap, or staff availability.")
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
    demand_df, staff_df, avail_df, leave_df = load_data(DB_PATH)

    print("Building indices...")
    (demand, staff_ids, wage, guaranteed, eff_guarantee,
     avail_set, leave_set, leave_days, calendar) = build_indices(
        demand_df, staff_df, avail_df, leave_df
    )
    print(f"  {len(staff_ids)} staff  |  {HORIZON} days  |  {N_SLOTS} slots/day")
    print(f"  {len(demand)} demand entries  |  {len(avail_set)} availability entries  |  {len(leave_set)} leave-days")

    # Tier 1: pre-solve availability gap check
    check_availability_gaps(demand, staff_ids, avail_set, leave_set, calendar)

    print("\nBuilding and solving ILP...")
    prob, x, status = build_and_solve(
        demand, staff_ids, wage, guaranteed, eff_guarantee, avail_set, leave_set, calendar
    )

    status_str = pulp.LpStatus[status]
    print(f"\nSolver status: {status_str}")

    if status_str != "Optimal":
        print("No usable solution returned.")
        answer = input("\nRun relaxed model to identify coverage shortfalls? [y/N] ").strip().lower()
        if answer == "y":
            print("Solving relaxed model (minimising unmet demand)...")
            slack, r_status = solve_relaxed(demand, staff_ids, avail_set, leave_set, calendar)
            if pulp.LpStatus[r_status] == "Optimal":
                print_relaxed_report(slack, demand, calendar)
            else:
                print("Relaxed model also infeasible — check hard constraints (availability, break rules).")
        return

    print(f"  Best solution within {GAP_REL:.0%} gap tolerance / {TIME_LIMIT}s cap "
          "(near-optimal; see the solver 'Result' line above for gap-vs-time stop).")

    schedule_df = extract_schedule(x, staff_ids, calendar)

    out = Path(__file__).parent.parent / "outputs" / "schedule.csv"
    schedule_df.to_csv(out, index=False)
    print(f"Schedule saved to {out}  ({len(schedule_df)} rows)")

    print_summary(schedule_df, staff_ids, wage, guaranteed, eff_guarantee, leave_days)
    check_coverage(schedule_df, demand, calendar)


if __name__ == "__main__":
    main()
