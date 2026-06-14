"""
Load raw Cafe Ocean Excel data into DuckDB.

Usage:
    python src/load_raw.py                 # incremental: only new rows
    python src/load_raw.py --full-refresh  # drop and reload everything
"""

import argparse
import pandas as pd
import duckdb
from pathlib import Path

# Specify the paths to the raw Excel file and the DuckDB database file.
RAW_FILE = Path("data/raw/Cafe_Ocean.xlsx")
DB_FILE  = Path("data/cafe_ocean.duckdb")

# Map original Excel column names to normalized column names for the database.
COLUMN_RENAME = {
    "Date":        "date",
    "Bill Number ": "bill_number", # There's a space after "Number" in the original column name.
    "Item Desc":   "item_desc",
    "Time":        "transaction_time",
    "Quantity":    "quantity",
    "Rate":        "rate",
    "Tax":         "tax",
    "Discount":    "discount",
    "Total":       "total",
    "Category":    "category",
}

# Helper function to check if a table exists in the DuckDB database.
def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    result = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()[0]
    return result > 0

# Read the raw Excel file, rename columns, and normalise text fields.
def _read_and_normalise() -> pd.DataFrame:
    print(f"Reading {RAW_FILE} ...")
    df = pd.read_excel(RAW_FILE, sheet_name="Sheet1", dtype={"Time": str})
    df = df.rename(columns=COLUMN_RENAME)

    for col in ("item_desc", "category", "bill_number"):
        df[col] = df[col].str.strip().str.upper()

    return df

# Main function to load data into DuckDB, with optional full refresh or incremental load.
def load_raw_data(full_refresh: bool = False) -> None:
    df = _read_and_normalise()

    print(f"  Rows in file : {len(df):,}")
    print(f"  Date range   : {df['date'].min().date()} → {df['date'].max().date()}")
    print(f"  Null counts  :\n{df.isnull().sum().to_string()}")

    con = duckdb.connect(str(DB_FILE))

    if full_refresh or not _table_exists(con, "raw_transactions"):
        mode = "Full refresh" if full_refresh else "First load"
        print(f"\n{mode} — writing all {len(df):,} rows to {DB_FILE} ...")
        con.execute("CREATE OR REPLACE TABLE raw_transactions AS SELECT * FROM df")

    else:
        max_date = con.execute("SELECT MAX(date) FROM raw_transactions").fetchone()[0]
        df_new = df[df["date"] > pd.Timestamp(max_date)]

        if df_new.empty:
            print(f"\nIncremental load — no new rows after {max_date}. Nothing to insert.")
            con.close()
            return

        print(f"\nIncremental load — inserting {len(df_new):,} new rows after {max_date} ...")
        con.execute("INSERT INTO raw_transactions SELECT * FROM df_new")

    row_count = con.execute("SELECT COUNT(*) FROM raw_transactions").fetchone()[0]
    con.close()

    print(f"  raw_transactions: {row_count:,} rows total.")
    print("Done.")

# Entry point for command-line execution, allowing for optional full refresh or incremental load.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load Cafe Ocean raw data into DuckDB.")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Drop and reload the entire table instead of appending new rows.",
    )
    args = parser.parse_args()
    load_raw_data(full_refresh=args.full_refresh)
