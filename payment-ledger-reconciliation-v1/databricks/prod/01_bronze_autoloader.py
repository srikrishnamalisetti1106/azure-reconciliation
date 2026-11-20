# Databricks notebook source
# MAGIC %md
# MAGIC # 01 Bronze: Auto Loader ingestion
# MAGIC Picks up only NEW files from the landing zone (Auto Loader remembers what it has
# MAGIC read in its checkpoint) and appends them, untouched, to bronze tables.
# MAGIC Every row keeps its source file and load time, so any number can be traced back to a file.
# MAGIC `availableNow` = process everything new, then stop (batch cost, streaming bookkeeping).

# COMMAND ----------

from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "recon_dev")
dbutils.widgets.text("landing_path", "/Volumes/recon_dev/bronze/landing/")
dbutils.widgets.text("checkpoint_path", "/Volumes/recon_dev/bronze/checkpoints/")
CATALOG = dbutils.widgets.get("catalog")
LANDING = dbutils.widgets.get("landing_path").rstrip("/")
CHECKPOINTS = dbutils.widgets.get("checkpoint_path").rstrip("/")

# Expected layout: <landing>/payments/*.csv, <landing>/ledger/*.csv, <landing>/fees/*.csv
ENTITIES = ["payments", "ledger", "fees"]

# COMMAND ----------

for entity in ENTITIES:
    stream = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("header", "true")
        # Read everything as string in bronze; typing happens in silver.
        .option("cloudFiles.inferColumnTypes", "false")
        .option("cloudFiles.schemaLocation", f"{CHECKPOINTS}/{entity}/schema")
        # A new column in a file lands in _rescued_data instead of failing the run.
        .option("cloudFiles.schemaEvolutionMode", "rescue")
        .load(f"{LANDING}/{entity}/")
        .withColumn("_source_file", F.col("_metadata.file_path"))
        .withColumn("_ingested_at", F.current_timestamp())
    )
    query = (
        stream.writeStream
        .option("checkpointLocation", f"{CHECKPOINTS}/{entity}/stream")
        .trigger(availableNow=True)
        .toTable(f"{CATALOG}.bronze.{entity}_raw")
    )
    query.awaitTermination()
    print(f"{entity}: bronze up to date")
