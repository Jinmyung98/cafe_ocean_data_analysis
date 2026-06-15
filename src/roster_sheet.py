"""
Roster sheet generator for Cafe Ocean.
Turns the per-slot schedule (outputs/schedule.csv) into a distributable roster:
collapses contiguous slots into shift blocks and writes a readable weekly grid.

Run AFTER staff_optimiser.py.

Usage:
    python src/roster_sheet.py

Outputs:
    outputs/roster.md   -- weekly grids + per-staff totals (human-readable, printable)
    outputs/roster.csv  -- staff x date grid (for spreadsheets)
"""

import datetime
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH   = Path(__file__).parent.parent / "data" / "cafe_ocean.duckdb"
SCHED_CSV = Path(__file__).parent.parent / "outputs" / "schedule.csv"
OUT_DIR   = Path(__file__).parent.parent / "outputs"
TAU       = 0.5

# 30 operating slots: 10:00 .. 00:30
SLOT_LABELS = [f"{h:02d}:{m:02d}:00" for h in range(10, 24) for m in (0, 30)]
SLOT_LABELS += ["00:00:00", "00:30:00"]


def _hhmm(label: str) -> str:
    return label[:5]


def slot_start(p: int) -> str:
    return _hhmm(SLOT_LABELS[p])


def slot_end(p: int) -> str:
    # a slot starting at p ends where the next slot begins; last slot (00:30) ends 01:00
    return _hhmm(SLOT_LABELS[p + 1]) if p + 1 < len(SLOT_LABELS) else "01:00"


def to_blocks(slot_positions):
    """Collapse sorted slot positions into contiguous (start, end) time blocks.
    A break shows up as a gap, so a shift renders as e.g. '17:00-20:00, 20:30-00:30'."""
    sp = sorted(slot_positions)
    runs = []
    run_start = prev = sp[0]
    for p in sp[1:]:
        if p == prev + 1:
            prev = p
        else:
            runs.append((run_start, prev))
            run_start = prev = p
    runs.append((run_start, prev))
    return [(slot_start(a), slot_end(b)) for a, b in runs]


def cell_text(slot_positions) -> str:
    return ", ".join(f"{a}-{b}" for a, b in to_blocks(slot_positions))


# ---------------------------------------------------------------------------
def load():
    if not SCHED_CSV.exists():
        raise SystemExit(f"{SCHED_CSV} not found — run staff_optimiser.py first.")
    sched = pd.read_csv(SCHED_CSV)
    sched["date"] = sched["date"].apply(lambda v: datetime.date.fromisoformat(str(v)[:10]))

    con = duckdb.connect(str(DB_PATH), read_only=True)
    staff = con.execute("SELECT staff_id, staff_name, role FROM main.dim_staff ORDER BY staff_id").df()
    leave = con.execute("SELECT staff_id, leave_date FROM main.bridge_staff_leave").df()
    con.close()
    leave_set = {
        (r.staff_id, datetime.date.fromisoformat(str(r.leave_date)[:10]))
        for r in leave.itertuples()
    }
    return sched, staff, leave_set


def build_grid(sched, staff, leave_set):
    # worked[(staff_id, date)] = [slot_pos, ...]
    worked = {}
    for (sid, date), grp in sched.groupby(["staff_id", "date"]):
        worked[(sid, date)] = list(grp["slot_pos"])

    dates = sorted(sched["date"].unique())
    start = dates[0]
    fortnight = [start + datetime.timedelta(days=i) for i in range(14)]

    rows = []
    for r in staff.itertuples():
        sid = r.staff_id
        row = {"staff_id": sid, "staff_name": r.staff_name, "role": r.role}
        total_slots = 0
        for date in fortnight:
            if (sid, date) in leave_set:
                row[date] = "LEAVE"
            elif (sid, date) in worked:
                slots = worked[(sid, date)]
                total_slots += len(slots)
                row[date] = cell_text(slots)
            else:
                row[date] = "OFF"
        row["total_hours"] = total_slots * TAU
        row["leave_days"]  = sum(1 for date in fortnight if (sid, date) in leave_set)
        rows.append(row)
    return rows, fortnight


def write_markdown(rows, fortnight):
    lines = ["# Cafe Ocean — Fortnight Roster", ""]
    lines.append(f"Planning period: **{fortnight[0]:%a %d %b %Y} – {fortnight[-1]:%a %d %b %Y}**")
    lines.append("")

    def week_table(week_dates, title):
        out = [f"## {title}", ""]
        header = "| Staff | " + " | ".join(f"{d:%a}<br>{d:%d/%m}" for d in week_dates) + " |"
        sep    = "|---|" + "|".join(["---"] * len(week_dates)) + "|"
        out += [header, sep]
        for row in rows:
            label = f"{row['staff_id']} {row['staff_name']} ({row['role']})"
            cells = " | ".join(str(row[d]) for d in week_dates)
            out.append(f"| {label} | {cells} |")
        out.append("")
        return out

    lines += week_table(fortnight[:7],  "Week 1")
    lines += week_table(fortnight[7:], "Week 2")

    lines += ["## Per-staff totals", "",
              "| Staff | Role | Hours | Leave days |", "|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {row['staff_id']} {row['staff_name']} | {row['role']} "
            f"| {row['total_hours']:.1f} | {row['leave_days']} |"
        )
    lines.append("")
    lines.append(f"_Total scheduled hours: {sum(r['total_hours'] for r in rows):.1f}_")
    lines.append("")

    out = OUT_DIR / "roster.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_csv(rows, fortnight):
    records = []
    for row in rows:
        rec = {"staff_id": row["staff_id"], "staff_name": row["staff_name"], "role": row["role"]}
        for d in fortnight:
            rec[d.isoformat()] = row[d]
        rec["total_hours"] = row["total_hours"]
        rec["leave_days"]  = row["leave_days"]
        records.append(rec)
    out = OUT_DIR / "roster.csv"
    pd.DataFrame(records).to_csv(out, index=False)
    return out


def main():
    sched, staff, leave_set = load()
    rows, fortnight = build_grid(sched, staff, leave_set)
    md  = write_markdown(rows, fortnight)
    csv = write_csv(rows, fortnight)
    print(f"Roster written:\n  {md}\n  {csv}")
    print(f"\n{len(rows)} staff over {len(fortnight)} days "
          f"| total {sum(r['total_hours'] for r in rows):.1f} h "
          f"| {sum(r['leave_days'] for r in rows)} leave-days")


if __name__ == "__main__":
    main()
