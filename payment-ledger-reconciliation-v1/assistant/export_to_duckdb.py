"""
Build a local, read-only copy of the Gold and reporting tables in DuckDB.

Why: while you develop the AI assistant you don't want to spend Databricks
compute on every question. This runs the same matching code on the generated
CSVs and saves the results to local/recon.duckdb, which the tools read.

Run from the repo root (after `python data/synthetic/generate.py`):
    python assistant/export_to_duckdb.py
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconciliation.matching import load_csvs, reconcile  # noqa: E402

DATA = ROOT / "data/synthetic/output"
DB = ROOT / "local/recon.duckdb"
BUSINESS_DATE = date(2026, 11, 2)
RUN_ID = "RUN-LOCAL"


def main() -> None:
    spark = (SparkSession.builder.master("local[*]").appName("export")
             .config("spark.sql.shuffle.partitions", "8").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    payments, ledger, fees = load_csvs(spark, str(DATA))
    result = reconcile(payments, ledger, fees)

    # KPI snapshots at T+1 and T+3, same logic as databricks/04_kpis
    ledger = ledger.withColumn("posting_date", F.to_date("posting_date"))
    n_total = payments.count()
    v_total = payments.agg(F.sum("amount_cents")).first()[0]
    kpis = []
    for cutoff, days in [("T+1", 1), ("T+3", 3)]:
        visible = ledger.filter(F.col("posting_date") <= F.lit(BUSINESS_DATE + timedelta(days=days)))
        matched = reconcile(payments, visible, fees).matches \
            .select("payment_id", "payment_amount_cents").distinct()
        n_m = matched.count()
        v_m = matched.agg(F.sum("payment_amount_cents")).first()[0]
        kpis += [(str(BUSINESS_DATE), cutoff, "transaction_rate", round(100 * n_m / n_total, 2)),
                 (str(BUSINESS_DATE), cutoff, "dollar_rate", round(100 * v_m / v_total, 2))]

    frames = {
        "payments": payments.toPandas(),
        "ledger": ledger.toPandas(),
        "fees": fees.toPandas(),
        "reconciliation_matches": result.matches.withColumn("run_id", F.lit(RUN_ID)).toPandas(),
        "reconciliation_exceptions": result.exceptions.withColumn("run_id", F.lit(RUN_ID)).toPandas(),
        "kpi_snapshots": spark.createDataFrame(
            kpis, "business_date string, cutoff string, metric string, value double").toPandas(),
    }
    spark.stop()

    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    con = duckdb.connect(str(DB))
    for name, df in frames.items():
        con.register("df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        con.unregister("df")
        print(f"{name}: {len(df)} rows")
    con.close()
    print("Wrote", DB)


if __name__ == "__main__":
    main()
