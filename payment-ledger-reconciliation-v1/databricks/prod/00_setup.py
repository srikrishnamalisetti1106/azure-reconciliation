# Databricks notebook source
# MAGIC %md
# MAGIC # 00 Setup
# MAGIC Creates the schemas and tables if they don't exist, and grants read-only access
# MAGIC to analysts. Safe to run every time (idempotent).

# COMMAND ----------

dbutils.widgets.text("catalog", "recon_dev")
CATALOG = dbutils.widgets.get("catalog")

for schema in ["bronze", "silver", "gold", "reporting"]:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{schema}")

# COMMAND ----------

# Gold and reporting tables, defined up front so MERGE always has a target.
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.gold.reconciliation_matches (
  match_id STRING, rule_id STRING, payment_id STRING, ledger_id STRING, reference STRING,
  currency STRING, payment_amount_cents BIGINT, ledger_amount_cents BIGINT, fee_cents BIGINT,
  run_id STRING, matched_at TIMESTAMP)
""")
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.gold.reconciliation_exceptions (
  exception_id STRING, reason_code STRING, reference STRING, currency STRING,
  payment_ids STRING, ledger_ids STRING, payment_total_cents BIGINT, ledger_total_cents BIGINT,
  exposure_cents BIGINT, status STRING, first_seen_run_id STRING, run_id STRING,
  updated_at TIMESTAMP)
""")
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.reporting.kpi_snapshots (
  business_date STRING, cutoff STRING, metric STRING, value DOUBLE,
  run_id STRING, published_at TIMESTAMP)
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Access
# MAGIC Analysts (and the assistant's service principal) can only read gold and reporting.
# MAGIC Only the pipeline's service principal writes. Group names come from Entra ID via SCIM.

# COMMAND ----------

for stmt in [
    f"GRANT USE CATALOG ON CATALOG {CATALOG} TO `recon-analysts`",
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {CATALOG}.gold TO `recon-analysts`",
    f"GRANT USE SCHEMA, SELECT ON SCHEMA {CATALOG}.reporting TO `recon-analysts`",
]:
    try:
        spark.sql(stmt)
    except Exception as e:  # group may not exist in a dev workspace
        print(f"skipped: {stmt} ({e})")
