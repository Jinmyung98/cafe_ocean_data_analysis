"""
Shared data loaders and styling helpers for the Cafe Ocean Dash app.

Reads the DuckDB warehouse read-only and the optimiser output CSVs, and exposes
small, cached accessors the pages build on. Marts and seeds are static, so their
loaders are memoised; the output CSVs are read fresh each call so a re-run of an
optimiser shows up without restarting the app.

Time columns (DuckDB TIME -> datetime.time) are normalised to "HH:MM" here, and
the slot -> shift-block logic mirrors src/roster_sheet.py so the roster page reads
the schedule exactly the way the roster sheet does.
"""

from __future__ import annotations

import datetime
import threading
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT      = Path(__file__).resolve().parent.parent
DB_PATH   = ROOT / "data" / "cafe_ocean.duckdb"
OUT_DIR   = ROOT / "outputs"
SCHED_CSV = OUT_DIR / "schedule.csv"
PO_CSV    = OUT_DIR / "purchase_order.csv"

# ---------------------------------------------------------------------------
# Slot grid (must match src/staff_optimiser.py / src/roster_sheet.py)
# 30 operating slots in order: 10:00, 10:30, ..., 23:30, 00:00, 00:30
# ---------------------------------------------------------------------------
TAU = 0.5  # hours per slot
SLOT_LABELS = [f"{h:02d}:{m:02d}:00" for h in range(10, 24) for m in (0, 30)]
SLOT_LABELS += ["00:00:00", "00:30:00"]
N_SLOTS = len(SLOT_LABELS)

# day_of_week follows DuckDB DAYOFWEEK: 0 = Sunday .. 6 = Saturday
DOW_NAMES  = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DOW_SHORT  = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

MAX_EXTRA_HOURS = 16   # fortnightly cap = guaranteed + this (matches staff_optimiser)
SAFETY_STOCK    = 0.20  # sigma used by stock_optimiser pre-check

# ---------------------------------------------------------------------------
# Shared colour palette
# ---------------------------------------------------------------------------
# Ocean-leaning qualitative palette, reused everywhere for a consistent look.
PALETTE = [
    "#1b6ca8", "#0c9eb3", "#13b196", "#7cb518", "#f4a259",
    "#e76f51", "#9b5de5", "#577590", "#c08552", "#2a9d8f",
]
ACCENT     = "#1b6ca8"
TEAL       = "#0c9eb3"
GREEN      = "#13b196"
AMBER      = "#f4a259"
GOOD       = "#2a9d8f"
WARN       = "#f4a259"
BAD        = "#e76f51"
GRID       = "#e6ecf2"
INK        = "#1f2d3d"
MUTED      = "#6b7c8f"

# Font stacks for in-chart text (Plotly needs concrete names; mirror style.css).
SANS_FONT  = "Inter, 'Segoe UI', system-ui, sans-serif"
SERIF_FONT = "Fraunces, Georgia, 'Times New Roman', serif"
MONO_FONT  = "IBM Plex Mono, Consolas, monospace"

CATEGORY_COLORS = {
    "FOOD":             "#f4a259",
    "BEVERAGE":         "#0c9eb3",
    "WINES":            "#9b5de5",
    "LIQUOR":           "#577590",
    "LIQUOR & TPBACCO": "#c08552",
    "TOBACCO":          "#6b7c8f",
    "MERCHANDISE":      "#7cb518",
    "MISC":             "#b0b8c1",
}

ROLE_COLORS = {
    "manager":   "#1b6ca8",
    "bartender": "#0c9eb3",
    "server":    "#13b196",
    "barista":   "#f4a259",
    "kitchen":   "#e76f51",
    "LEAVE":     "#d9c2c0",
}


def category_color(cat: str) -> str:
    return CATEGORY_COLORS.get(cat, MUTED)


def role_color(role: str) -> str:
    return ROLE_COLORS.get(role, MUTED)


# ---------------------------------------------------------------------------
# Time / slot helpers
# ---------------------------------------------------------------------------
def hhmm(t) -> str:
    """Normalise a TIME value (datetime.time, timedelta, or str) to 'HH:MM'."""
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return ""
    if isinstance(t, str):
        return t[:5]
    if isinstance(t, datetime.time):
        return t.strftime("%H:%M")
    if isinstance(t, datetime.timedelta):  # DuckDB occasionally returns TIME as timedelta
        total = int(t.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"
    return str(t)[:5]


def slot_start_hhmm(pos: int) -> str:
    return SLOT_LABELS[pos][:5]


def slot_end_hhmm(pos: int) -> str:
    """A slot ends where the next begins; the final slot (00:30) ends at 01:00."""
    return SLOT_LABELS[pos + 1][:5] if pos + 1 < N_SLOTS else "01:00"


def to_blocks(slot_positions) -> list[tuple[str, str]]:
    """Collapse sorted slot positions into contiguous (start, end) time blocks.

    Mirrors src/roster_sheet.py: a break shows up as a gap, so a shift renders as
    e.g. [('17:00', '20:00'), ('20:30', '00:30')].
    """
    sp = sorted(slot_positions)
    if not sp:
        return []
    runs = []
    run_start = prev = sp[0]
    for p in sp[1:]:
        if p == prev + 1:
            prev = p
        else:
            runs.append((run_start, prev))
            run_start = prev = p
    runs.append((run_start, prev))
    return [(slot_start_hhmm(a), slot_end_hhmm(b)) for a, b in runs]


def block_datetimes(work_date: datetime.date, start_hhmm: str, end_hhmm: str):
    """Turn a ('HH:MM', 'HH:MM') block on an operating date into real datetimes.

    The cafe day runs 10:00 -> 01:00, so any time before 10:00 (00:00, 00:30,
    01:00) belongs to the following calendar morning; bump it a day so a shift
    that crosses midnight plots as one continuous bar.
    """
    def _dt(hm: str) -> datetime.datetime:
        h, m = int(hm[:2]), int(hm[3:5])
        d = work_date + datetime.timedelta(days=1) if h < 10 else work_date
        return datetime.datetime(d.year, d.month, d.day, h, m)

    return _dt(start_hhmm), _dt(end_hhmm)


# ---------------------------------------------------------------------------
# DuckDB access (single read-only connection, guarded for callback threads)
# ---------------------------------------------------------------------------
_con_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None


def _query(sql: str) -> pd.DataFrame:
    global _con
    with _con_lock:
        if _con is None:
            _con = duckdb.connect(str(DB_PATH), read_only=True)
        return _con.execute(sql).df()


# ---------------------------------------------------------------------------
# Cached warehouse loaders (marts + seeds are static)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_transactions() -> pd.DataFrame:
    """Line items joined to item attributes, with date parts for the operations page."""
    df = _query("""
        SELECT
            f.bill_number,
            f.date,
            f.transaction_time,
            f.quantity,
            f.total,
            f.item_id,
            i.item_name,
            i.category
        FROM main_marts.fact_transactions f
        JOIN main_marts.dim_items i USING (item_id)
    """)
    df["date"] = pd.to_datetime(df["date"])
    df["dow"] = (df["date"].dt.dayofweek + 1) % 7          # pandas Mon=0 -> DuckDB Sun=0
    df["hour"] = df["transaction_time"].apply(
        lambda t: t.hour if isinstance(t, datetime.time) else None
    )
    df["category"] = df["category"].fillna("MISC")
    return df


@lru_cache(maxsize=1)
def load_demand_by_slot() -> pd.DataFrame:
    df = _query("""
        SELECT day_of_week, slot_start, avg_bills, p75_bills, min_staff
        FROM main_marts.ref_demand_by_slot
    """)
    df["slot"] = df["slot_start"].apply(hhmm)
    df["min_staff"] = df["min_staff"].astype(int)
    return df


@lru_cache(maxsize=1)
def load_staff() -> pd.DataFrame:
    return _query("""
        SELECT staff_id, staff_name, role,
               guaranteed_hours_per_fortnight AS guaranteed_hours,
               hourly_rate
        FROM main.dim_staff
        ORDER BY staff_id
    """)


@lru_cache(maxsize=1)
def load_leave() -> pd.DataFrame:
    df = _query("SELECT staff_id, leave_date, leave_type FROM main.bridge_staff_leave")
    df["leave_date"] = pd.to_datetime(df["leave_date"]).dt.date
    return df


@lru_cache(maxsize=1)
def load_suppliers() -> pd.DataFrame:
    return _query("""
        SELECT supplier_id, supplier_name, lead_time_days, contact
        FROM main.dim_suppliers
    """)


@lru_cache(maxsize=1)
def load_capacity() -> pd.DataFrame:
    """One row per ingredient: storage location(s) and total max quantity."""
    return _query("""
        SELECT ingredient_id,
               STRING_AGG(DISTINCT location, ', ') AS location,
               SUM(max_quantity)                   AS max_quantity
        FROM main.ref_stockroom_capacity
        GROUP BY ingredient_id
    """)


@lru_cache(maxsize=1)
def load_ingredient_demand() -> pd.DataFrame:
    return _query("""
        SELECT ingredient_id, ingredient_name, unit, shelf_life_days,
               purchase_unit_size, purchase_unit_cost, supplier_id,
               avg_weekly_units, p75_weekly_units, weeks_observed
        FROM main_marts.ref_ingredient_demand_weekly
        ORDER BY ingredient_id
    """)


@lru_cache(maxsize=1)
def load_ingredient_category() -> pd.DataFrame:
    """Map each ingredient to the menu category it most often feeds, via the BOM.

    Ingredients have no category of their own, so we borrow the dominant category
    of the menu items that consume them (almost all map 1:1).
    """
    return _query("""
        WITH usage AS (
            SELECT b.ingredient_id, i.category, COUNT(*) AS n
            FROM main.bridge_bill_of_materials b
            JOIN main_marts.dim_items i USING (item_id)
            GROUP BY b.ingredient_id, i.category
        ),
        ranked AS (
            SELECT ingredient_id, category,
                   ROW_NUMBER() OVER (PARTITION BY ingredient_id ORDER BY n DESC) AS rk
            FROM usage
        )
        SELECT ingredient_id, category FROM ranked WHERE rk = 1
    """)


# ---------------------------------------------------------------------------
# Optimiser outputs (read fresh; may be missing before an optimiser is run)
# ---------------------------------------------------------------------------
def load_schedule() -> pd.DataFrame | None:
    """Per-(staff, date, slot) roster from staff_optimiser; None if not yet run."""
    if not SCHED_CSV.exists():
        return None
    df = pd.read_csv(SCHED_CSV)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["slot"] = df["slot_start"].apply(hhmm)
    return df


def load_purchase_order() -> pd.DataFrame | None:
    """Weekly purchase order from stock_optimiser; None if not yet run."""
    if not PO_CSV.exists():
        return None
    return pd.read_csv(PO_CSV)


# ---------------------------------------------------------------------------
# Stock feasibility pre-check (recomputed from the same rule as stock_optimiser)
# ---------------------------------------------------------------------------
def excluded_ingredients() -> pd.DataFrame:
    """Ingredients the stock optimiser drops before solving.

    Recomputes stock_optimiser.pre_check with current_stock = 0: an ingredient is
    excluded when its shelf life can't hold a week's cover, or storage can't.
    """
    import math

    dem = load_ingredient_demand()
    cap = load_capacity().set_index("ingredient_id")["max_quantity"].to_dict()

    rows = []
    for r in dem.itertuples():
        d   = r.p75_weekly_units
        tau = r.shelf_life_days
        c   = cap.get(r.ingredient_id, float("inf"))
        req       = math.ceil((1 + SAFETY_STOCK) * d)
        shelf_max = math.floor((tau / 7) * d)
        if shelf_max < req:
            rows.append({
                "ingredient_id": r.ingredient_id, "ingredient_name": r.ingredient_name,
                "unit": r.unit, "flag": "Shelf life",
                "detail": f"{tau}d shelf life holds {shelf_max} {r.unit}, but a week's cover needs {req}",
            })
        elif c < req:
            rows.append({
                "ingredient_id": r.ingredient_id, "ingredient_name": r.ingredient_name,
                "unit": r.unit, "flag": "Capacity",
                "detail": f"storage caps at {int(c)} {r.unit}, but a week's cover needs {req}",
            })
    return pd.DataFrame(rows, columns=["ingredient_id", "ingredient_name", "unit", "flag", "detail"])
