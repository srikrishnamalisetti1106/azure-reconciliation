"""
Deterministic payment-to-ledger matching rules (PySpark).

Every rule is a plain function that takes the records that are still unmatched
and returns match rows stamped with a rule ID and version. Rules run in a fixed
priority order; each one only sees what the previous rules left behind.

    EXACT_V1      same reference, currency and amount (one payment <-> one ledger line)
    FEE_AWARE_V1  ledger amount == payment amount - recorded processor fee
    SPLIT_V1      2+ ledger lines with the payment's reference sum exactly to it

Safety rules shared by all three:
  * A match is only accepted if it is UNIQUE: the payment has exactly one
    candidate and the candidate has exactly one payment. Anything with two or
    more candidates is left alone, so a human sees it as an exception.
  * Money is integer cents; no floating-point arithmetic.
  * match_id is built from rule ID + payment ID, so re-running the same input
    gives identical output (determinism).

Whatever no rule matches is tagged by `classify_exceptions` with a reason code.

Input columns expected (as written by data/synthetic/generate.py):
    payments: payment_id, processor_ref, amount_cents, currency
    ledger:   ledger_id, reference, amount_cents, currency
    fees:     processor_ref, fee_cents, currency
"""

from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

EXACT_V1 = "EXACT_V1"
FEE_AWARE_V1 = "FEE_AWARE_V1"
SPLIT_V1 = "SPLIT_V1"
RULE_ORDER = [EXACT_V1, FEE_AWARE_V1, SPLIT_V1]

# One row per (match, ledger line). A split match has several rows with the same match_id.
MATCH_COLUMNS = [
    "match_id", "rule_id", "payment_id", "ledger_id", "reference",
    "currency", "payment_amount_cents", "ledger_amount_cents", "fee_cents",
]


# --------------------------------------------------------------------------- helpers
def _unique_pairs(candidates: DataFrame) -> DataFrame:
    """Keep only payment/ledger pairs where each side has exactly one candidate."""
    per_payment = Window.partitionBy("payment_id")
    per_ledger = Window.partitionBy("ledger_id")
    return (
        candidates
        .withColumn("_n_led", F.count("*").over(per_payment))
        .withColumn("_n_pay", F.count("*").over(per_ledger))
        .filter((F.col("_n_led") == 1) & (F.col("_n_pay") == 1))
        .drop("_n_led", "_n_pay")
    )


def _stamp(df: DataFrame, rule_id: str) -> DataFrame:
    return (
        df.withColumn("rule_id", F.lit(rule_id))
        .withColumn("match_id", F.concat_ws(":", F.lit(rule_id), F.col("payment_id")))
        .select(*MATCH_COLUMNS)
    )


def _p(payments: DataFrame) -> DataFrame:
    return payments.select(
        "payment_id",
        F.col("processor_ref").alias("reference"),
        "currency",
        F.col("amount_cents").cast("long").alias("payment_amount_cents"),
    )


def _l(ledger: DataFrame) -> DataFrame:
    return ledger.select(
        "ledger_id",
        "reference",
        "currency",
        F.col("amount_cents").cast("long").alias("ledger_amount_cents"),
    )


# --------------------------------------------------------------------------- rules
def match_exact(payments: DataFrame, ledger: DataFrame) -> DataFrame:
    """EXACT_V1: same reference, same currency, same amount, and unique on both sides."""
    candidates = _p(payments).join(
        _l(ledger), on=["reference", "currency"], how="inner"
    ).filter(F.col("payment_amount_cents") == F.col("ledger_amount_cents"))
    return _stamp(_unique_pairs(candidates).withColumn("fee_cents", F.lit(0).cast("long")), EXACT_V1)


def match_fee_aware(payments: DataFrame, ledger: DataFrame, fees: DataFrame) -> DataFrame:
    """FEE_AWARE_V1: ledger amount == payment amount - total recorded fee for that reference."""
    fee_totals = (
        fees.groupBy(F.col("processor_ref").alias("reference"), "currency")
        .agg(F.sum(F.col("fee_cents").cast("long")).alias("fee_cents"))
    )
    candidates = (
        _p(payments)
        .join(fee_totals, on=["reference", "currency"], how="inner")
        .join(_l(ledger), on=["reference", "currency"], how="inner")
        .filter(F.col("payment_amount_cents") - F.col("fee_cents") == F.col("ledger_amount_cents"))
    )
    return _stamp(_unique_pairs(candidates), FEE_AWARE_V1)


def match_split(payments: DataFrame, ledger: DataFrame) -> DataFrame:
    """SPLIT_V1: one payment == exact sum of 2+ ledger lines with the same reference.

    Only accepted when exactly one unmatched payment carries that reference,
    so we never have to guess which payment the lines belong to.
    """
    p = _p(payments)
    single_payment_refs = (
        p.groupBy("reference", "currency").agg(F.count("*").alias("_n"))
        .filter(F.col("_n") == 1).drop("_n")
    )
    groups = (
        _l(ledger).groupBy("reference", "currency")
        .agg(F.count("*").alias("_lines"), F.sum("ledger_amount_cents").alias("_total"))
        .filter(F.col("_lines") >= 2)
    )
    ok_groups = (
        p.join(single_payment_refs, on=["reference", "currency"], how="inner")
        .join(groups, on=["reference", "currency"], how="inner")
        .filter(F.col("payment_amount_cents") == F.col("_total"))
        .select("reference", "currency", "payment_id", "payment_amount_cents")
    )
    members = ok_groups.join(_l(ledger), on=["reference", "currency"], how="inner")
    return _stamp(members.withColumn("fee_cents", F.lit(0).cast("long")), SPLIT_V1)


# --------------------------------------------------------------------------- runner
@dataclass
class ReconciliationResult:
    matches: DataFrame            # one row per matched ledger line
    exceptions: DataFrame         # one row per reference that didn't reconcile
    unmatched_payments: DataFrame
    unmatched_ledger: DataFrame


def reconcile(payments: DataFrame, ledger: DataFrame, fees: DataFrame) -> ReconciliationResult:
    """Run the rules in priority order, removing matched records before the next rule."""
    pay_left, led_left = payments, ledger
    all_matches = []

    for rule_id in RULE_ORDER:
        if rule_id == EXACT_V1:
            m = match_exact(pay_left, led_left)
        elif rule_id == FEE_AWARE_V1:
            m = match_fee_aware(pay_left, led_left, fees)
        else:
            m = match_split(pay_left, led_left)
        all_matches.append(m)
        pay_left = pay_left.join(m.select("payment_id").distinct(), "payment_id", "left_anti")
        led_left = led_left.join(m.select("ledger_id"), "ledger_id", "left_anti")

    matches = all_matches[0]
    for m in all_matches[1:]:
        matches = matches.unionByName(m)

    exceptions = classify_exceptions(pay_left, led_left, fees)
    return ReconciliationResult(matches, exceptions, pay_left, led_left)


# --------------------------------------------------------------------------- exceptions
def classify_exceptions(payments: DataFrame, ledger: DataFrame, fees: DataFrame) -> DataFrame:
    """Group leftovers by reference and give each group one reason code.

    Checked in this order (first true wins):
      AMBIGUOUS_MATCH       2+ payments share the reference and there is ledger activity
      MISSING_IN_LEDGER     payment(s) but no ledger lines
      MISSING_IN_PROCESSOR  ledger line(s) but no payment
      DUPLICATE_CANDIDATE   2+ ledger lines with the same amount as the payment
      FEE_MISMATCH          a fee is recorded but doesn't explain the difference
      SPLIT_SUM_MISMATCH    2+ ledger lines that don't sum to the payment
      AMOUNT_MISMATCH       anything else (one line, wrong amount, no fee)
    exposure_cents = |payment total - ledger total|, or the full amount if one side is missing.
    """
    p = (
        _p(payments).groupBy("reference", "currency").agg(
            F.count("*").alias("n_payments"),
            F.sum("payment_amount_cents").alias("payment_total_cents"),
            F.max("payment_amount_cents").alias("payment_amount_cents"),
            F.concat_ws(";", F.sort_array(F.collect_list("payment_id"))).alias("payment_ids"),
        )
    )
    l_rows = _l(ledger)
    led = (
        l_rows.groupBy("reference", "currency").agg(
            F.count("*").alias("n_ledger"),
            F.sum("ledger_amount_cents").alias("ledger_total_cents"),
            F.concat_ws(";", F.sort_array(F.collect_list("ledger_id"))).alias("ledger_ids"),
        )
    )
    same_amount = (
        _p(payments).join(l_rows, ["reference", "currency"])
        .filter(F.col("payment_amount_cents") == F.col("ledger_amount_cents"))
        .groupBy("reference", "currency").agg(F.countDistinct("ledger_id").alias("n_same_amount"))
    )
    fee_refs = fees.select(F.col("processor_ref").alias("reference"), "currency").distinct() \
        .withColumn("has_fee", F.lit(True))

    g = (
        p.join(led, ["reference", "currency"], "full_outer")
        .join(same_amount, ["reference", "currency"], "left")
        .join(fee_refs, ["reference", "currency"], "left")
        .fillna({"n_payments": 0, "n_ledger": 0, "payment_total_cents": 0,
                 "ledger_total_cents": 0, "n_same_amount": 0, "has_fee": False,
                 "payment_ids": "", "ledger_ids": ""})
    )
    reason = (
        F.when((F.col("n_payments") >= 2) & (F.col("n_ledger") >= 1), "AMBIGUOUS_MATCH")
        .when(F.col("n_ledger") == 0, "MISSING_IN_LEDGER")
        .when(F.col("n_payments") == 0, "MISSING_IN_PROCESSOR")
        .when(F.col("n_same_amount") >= 2, "DUPLICATE_CANDIDATE")
        .when(F.col("has_fee"), "FEE_MISMATCH")
        .when(F.col("n_ledger") >= 2, "SPLIT_SUM_MISMATCH")
        .otherwise("AMOUNT_MISMATCH")
    )
    exposure = (
        F.when(F.col("n_ledger") == 0, F.col("payment_total_cents"))
        .when(F.col("n_payments") == 0, F.col("ledger_total_cents"))
        .otherwise(F.abs(F.col("payment_total_cents") - F.col("ledger_total_cents")))
    )
    return (
        g.withColumn("reason_code", reason)
        .withColumn("exposure_cents", exposure)
        .withColumn("exception_id", F.concat_ws(":", F.lit("EXC"), F.col("reference")))
        .select("exception_id", "reason_code", "reference", "currency", "payment_ids",
                "ledger_ids", "payment_total_cents", "ledger_total_cents", "exposure_cents")
        .orderBy("exception_id")
    )


# --------------------------------------------------------------------------- local run
def load_csvs(spark: SparkSession, folder: str) -> tuple[DataFrame, DataFrame, DataFrame]:
    read = lambda name: spark.read.option("header", True).option("inferSchema", True).csv(f"{folder}/{name}")
    return read("payments.csv"), read("ledger.csv"), read("fees.csv")


if __name__ == "__main__":
    import sys

    folder = sys.argv[1] if len(sys.argv) > 1 else "data/synthetic/output"
    spark = SparkSession.builder.master("local[*]").appName("recon").config("spark.sql.shuffle.partitions", "8").getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    payments, ledger, fees = load_csvs(spark, folder)
    result = reconcile(payments, ledger, fees)

    print("Matches by rule:")
    result.matches.groupBy("rule_id").agg(
        F.countDistinct("payment_id").alias("payments"), F.count("*").alias("ledger_lines")
    ).orderBy("rule_id").show()
    print("Exceptions:")
    result.exceptions.show(truncate=False)
    spark.stop()
