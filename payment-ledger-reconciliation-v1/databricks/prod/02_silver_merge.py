# Databricks notebook source
# MAGIC %md
# MAGIC # 02 Silver: quality gate + MERGE
# MAGIC 1. Cast bronze strings to real types.
# MAGIC 2. Rows that fail a check go to a quarantine table with the reason (never silently dropped).
# MAGIC 3. Duplicates of the same key keep only the latest load.
# MAGIC 4. MERGE into silver, so re-running or re-sending a file updates rows instead of doubling them.

# COMMAND ----------

from delta.tables import DeltaTable
from pyspark.sql import functions as F
from pyspark.sql.window import Window

dbutils.widgets.text("catalog", "recon_dev")
dbutils.widgets.text("run_id", "manual")
CATALOG = dbutils.widgets.get("catalog")
RUN_ID = dbutils.widgets.get("run_id")

# key column, typed columns, and the checks each row must pass
SPECS = {
    "payments": {
        "key": "payment_id",
        "cast": {"amount_cents": "bigint", "event_time": "timestamp"},
        "checks": {
            "missing_key": F.col("payment_id").isNull(),
            "missing_reference": F.col("processor_ref").isNull(),
            "bad_amount": F.col("amount_cents").isNull() | (F.col("amount_cents") <= 0),
            "bad_currency": ~F.col("currency").rlike("^[A-Z]{3}$"),
        },
    },
    "ledger": {
        "key": "ledger_id",
        "cast": {"amount_cents": "bigint", "posting_date": "date"},
        "checks": {
            "missing_key": F.col("ledger_id").isNull(),
            "missing_reference": F.col("reference").isNull(),
            "bad_amount": F.col("amount_cents").isNull(),
            "bad_posting_date": F.col("posting_date").isNull(),
        },
    },
    "fees": {
        "key": "fee_id",
        "cast": {"fee_cents": "bigint"},
        "checks": {
            "missing_key": F.col("fee_id").isNull(),
            "bad_fee": F.col("fee_cents").isNull() | (F.col("fee_cents") < 0),
        },
    },
}

# COMMAND ----------

def first_failed_check(checks: dict):
    """Name of the first failing check, or null if the row is clean."""
    expr = F.lit(None).cast("string")
    for name, failed in reversed(list(checks.items())):
        expr = F.when(F.coalesce(failed, F.lit(True)), F.lit(name)).otherwise(expr)
    return expr


for entity, spec in SPECS.items():
    df = spark.table(f"{CATALOG}.bronze.{entity}_raw")
    for col, typ in spec["cast"].items():
        df = df.withColumn(col, F.col(col).try_cast(typ))
    df = df.withColumn("_dq_failure", first_failed_check(spec["checks"]))

    bad = df.filter(F.col("_dq_failure").isNotNull()).withColumn("_run_id", F.lit(RUN_ID))
    bad.write.mode("append").option("mergeSchema", "true").saveAsTable(f"{CATALOG}.silver.{entity}_quarantine")

    latest = Window.partitionBy(spec["key"]).orderBy(F.col("_ingested_at").desc())
    good = (df.filter(F.col("_dq_failure").isNull())
              .withColumn("_rn", F.row_number().over(latest))
              .filter("_rn = 1")
              .drop("_rn", "_dq_failure", "_rescued_data"))

    target = f"{CATALOG}.silver.{entity}"
    if not spark.catalog.tableExists(target):
        good.limit(0).write.saveAsTable(target)
    (DeltaTable.forName(spark, target).alias("t")
        .merge(good.alias("s"), f"t.{spec['key']} = s.{spec['key']}")
        .whenMatchedUpdateAll(condition="s._ingested_at > t._ingested_at")
        .whenNotMatchedInsertAll()
        .execute())
    print(f"{entity}: {bad.count()} quarantined, silver merged")
