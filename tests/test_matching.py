"""
Tests for reconciliation/matching.py.

Run from the repo root:
    python data/synthetic/generate.py
    pytest -q
"""

import csv
import subprocess
import sys
from pathlib import Path

import pytest
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reconciliation.matching import load_csvs, reconcile  # noqa: E402


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    subprocess.run(
        [sys.executable, str(ROOT / "data/synthetic/generate.py"), "--out", str(out)],
        check=True,
    )
    return out


@pytest.fixture(scope="session")
def spark():
    s = SparkSession.builder.master("local[2]").appName("recon-tests").config("spark.sql.shuffle.partitions", "8").getOrCreate()
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


@pytest.fixture(scope="session")
def result(spark, data_dir):
    payments, ledger, fees = load_csvs(spark, str(data_dir))
    return reconcile(payments, ledger, fees)


def test_every_planted_break_is_an_exception_with_the_right_code(result, data_dir):
    with open(data_dir / "expected.csv", newline="") as f:
        expected = {
            (row["expected_reason_code"], row["payment_ids"], row["ledger_ids"])
            for row in csv.DictReader(f)
        }
    actual = {
        (r.reason_code, r.payment_ids, r.ledger_ids)
        for r in result.exceptions.collect()
    }
    assert actual == expected  # nothing missed, nothing extra


def test_same_input_gives_same_output(spark, data_dir, result):
    payments, ledger, fees = load_csvs(spark, str(data_dir))
    again = reconcile(payments, ledger, fees)
    key = ["match_id", "ledger_id"]
    assert sorted(result.matches.select(*key).collect()) == sorted(again.matches.select(*key).collect())
    assert sorted(result.exceptions.collect()) == sorted(again.exceptions.collect())


def test_no_record_is_used_twice(result):
    assert result.matches.groupBy("ledger_id").count().filter("count > 1").count() == 0
    assert result.matches.groupBy("payment_id").agg(F.countDistinct("match_id").alias("n")) \
        .filter("n > 1").count() == 0


def test_every_match_has_a_rule_id(result):
    assert result.matches.filter(F.col("rule_id").isNull()).count() == 0


def test_money_ties_out(spark, data_dir, result):
    payments, ledger, _ = load_csvs(spark, str(data_dir))
    total_pay = payments.agg(F.sum("amount_cents")).first()[0]
    total_led = ledger.agg(F.sum("amount_cents")).first()[0]

    matched_pay = result.matches.select("payment_id", "payment_amount_cents").distinct() \
        .agg(F.sum("payment_amount_cents")).first()[0]
    matched_led = result.matches.agg(F.sum("ledger_amount_cents")).first()[0]
    exc = result.exceptions.agg(F.sum("payment_total_cents"), F.sum("ledger_total_cents")).first()

    assert matched_pay + exc[0] == total_pay
    assert matched_led + exc[1] == total_led
