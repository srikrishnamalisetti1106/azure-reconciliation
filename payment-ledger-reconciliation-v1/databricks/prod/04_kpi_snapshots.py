# Databricks notebook source
# MAGIC %md
# MAGIC # 04 KPI snapshots
# MAGIC Transaction and dollar match rates at T+1 and T+3, using only the ledger lines that
# MAGIC had posted by each cutoff. Snapshots are APPEND-only: a published number is never
# MAGIC overwritten, so last week's report can always be reproduced.

# COMMAND ----------

import os
import sys
from datetime import date, timedelta

from pyspark.sql import functions as F

sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..")))
from reconciliation.matching import reconcile  # noqa: E402

dbutils.widgets.text("catalog", "recon_dev")
dbutils.widgets.text("run_id", "manual")
dbutils.widgets.text("business_date", "")
CATALOG = dbutils.widgets.get("catalog")
RUN_ID = dbutils.widgets.get("run_id")

payments = spark.table(f"{CATALOG}.silver.payments").select(
    "payment_id", "processor_ref", "merchant_id", "amount_cents", "currency", "event_time", "status")
ledger = spark.table(f"{CATALOG}.silver.ledger").select(
    "ledger_id", "reference", "amount_cents", "currency", "posting_date", "entry_type", "account")
fees = spark.table(f"{CATALOG}.silver.fees").select(
    "fee_id", "processor_ref", "fee_cents", "currency", "fee_type")

bd = dbutils.widgets.get("business_date")
BUSINESS_DATE = date.fromisoformat(bd) if bd else payments.agg(F.max(F.to_date("event_time"))).first()[0]
day = payments.filter(F.to_date("event_time") == F.lit(BUSINESS_DATE))

# COMMAND ----------

n_total = day.count()
v_total = day.agg(F.sum("amount_cents")).first()[0] or 0
rows = []
for cutoff, days in [("T+1", 1), ("T+3", 3)]:
    visible = ledger.filter(F.col("posting_date") <= F.lit(BUSINESS_DATE + timedelta(days=days)))
    matched = (reconcile(day, visible, fees).matches
               .select("payment_id", "payment_amount_cents").distinct())
    n_m = matched.count()
    v_m = matched.agg(F.sum("payment_amount_cents")).first()[0] or 0
    rows += [(str(BUSINESS_DATE), cutoff, "transaction_rate", round(100 * n_m / max(n_total, 1), 2)),
             (str(BUSINESS_DATE), cutoff, "dollar_rate", round(100 * v_m / max(v_total, 1), 2))]

snap = (spark.createDataFrame(rows, "business_date string, cutoff string, metric string, value double")
        .withColumn("run_id", F.lit(RUN_ID))
        .withColumn("published_at", F.current_timestamp()))
snap.write.mode("append").saveAsTable(f"{CATALOG}.reporting.kpi_snapshots")

# Latest published value per KPI: what Power BI and the assistant read.
spark.sql(f"""
CREATE OR REPLACE VIEW {CATALOG}.reporting.kpi_latest AS
SELECT business_date, cutoff, metric, value, run_id, published_at FROM (
  SELECT *, row_number() OVER (PARTITION BY business_date, cutoff, metric ORDER BY published_at DESC) rn
  FROM {CATALOG}.reporting.kpi_snapshots) WHERE rn = 1
""")
display(snap)
