# Databricks notebook source
# MAGIC %md
# MAGIC # 03 Gold: run the versioned rules and MERGE results
# MAGIC Uses the SAME `reconciliation/matching.py` that the unit tests cover, so what CI
# MAGIC tested is exactly what production runs. Every row carries the run_id and rule_id.
# MAGIC Exceptions are tracked over time: new ones are OPEN, ones that disappear become RESOLVED.

# COMMAND ----------

import os
import sys

from delta.tables import DeltaTable
from pyspark.sql import functions as F

# The bundle syncs the repo, so the repo root is two folders up from this notebook.
sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..")))
from reconciliation.matching import reconcile  # noqa: E402

dbutils.widgets.text("catalog", "recon_dev")
dbutils.widgets.text("run_id", "manual")
CATALOG = dbutils.widgets.get("catalog")
RUN_ID = dbutils.widgets.get("run_id")

payments = spark.table(f"{CATALOG}.silver.payments").select(
    "payment_id", "processor_ref", "merchant_id", "amount_cents", "currency", "event_time", "status")
ledger = spark.table(f"{CATALOG}.silver.ledger").select(
    "ledger_id", "reference", "amount_cents", "currency", "posting_date", "entry_type", "account")
fees = spark.table(f"{CATALOG}.silver.fees").select(
    "fee_id", "processor_ref", "fee_cents", "currency", "fee_type")

result = reconcile(payments, ledger, fees)

# COMMAND ----------

now = F.current_timestamp()
matches = result.matches.withColumn("run_id", F.lit(RUN_ID)).withColumn("matched_at", now)

# A match never changes once made: insert new ones only (keeps the original run_id as lineage).
(DeltaTable.forName(spark, f"{CATALOG}.gold.reconciliation_matches").alias("t")
    .merge(matches.alias("s"), "t.match_id = s.match_id")
    .whenNotMatchedInsertAll()
    .execute())

# COMMAND ----------

exceptions = (result.exceptions
    .withColumn("status", F.lit("OPEN"))
    .withColumn("first_seen_run_id", F.lit(RUN_ID))
    .withColumn("run_id", F.lit(RUN_ID))
    .withColumn("updated_at", now))

(DeltaTable.forName(spark, f"{CATALOG}.gold.reconciliation_exceptions").alias("t")
    .merge(exceptions.alias("s"), "t.exception_id = s.exception_id")
    # still broken: refresh the numbers, keep first_seen_run_id
    .whenMatchedUpdate(set={
        "reason_code": "s.reason_code", "payment_ids": "s.payment_ids", "ledger_ids": "s.ledger_ids",
        "payment_total_cents": "s.payment_total_cents", "ledger_total_cents": "s.ledger_total_cents",
        "exposure_cents": "s.exposure_cents", "status": "'OPEN'", "run_id": "s.run_id",
        "updated_at": "s.updated_at"})
    .whenNotMatchedInsertAll()
    # no longer in this run's exceptions: it was fixed (e.g. the late ledger line arrived)
    .whenNotMatchedBySourceUpdate(
        condition="t.status = 'OPEN'",
        set={"status": "'RESOLVED'", "run_id": f"'{RUN_ID}'", "updated_at": "current_timestamp()"})
    .execute())

# COMMAND ----------

# Control check: every payment is either matched or in an open exception, never both, never neither.
open_exc = spark.table(f"{CATALOG}.gold.reconciliation_exceptions").filter("status = 'OPEN'")
in_exc = open_exc.select(F.explode(F.split("payment_ids", ";")).alias("payment_id")).filter("payment_id != ''")
in_match = result.matches.select("payment_id")
covered = in_exc.union(in_match).distinct().count()
total = payments.count()
assert covered == total, f"control failed: {covered} of {total} payments accounted for"
print(f"run {RUN_ID}: {result.matches.count()} matches, {result.exceptions.count()} exceptions, control OK")
