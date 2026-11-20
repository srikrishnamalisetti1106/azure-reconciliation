"""
Production backend for the read-only tools: the same four functions as tools.py,
with the same names, docstrings and JSON output, but reading the Gold and reporting
tables in Unity Catalog through a Databricks SQL warehouse.

Safety is the same as locally, plus two extra layers:
  * every query is fixed SQL with named parameters (:record_id); the model never writes SQL
  * the token belongs to a service principal that only has SELECT on gold/reporting/silver
  * results are capped at MAX_ROWS

Settings (environment variables, from Key Vault in Azure):
  DATABRICKS_SERVER_HOSTNAME  adb-....azuredatabricks.net
  DATABRICKS_HTTP_PATH        /sql/1.0/warehouses/<id>
  DATABRICKS_TOKEN            service principal token (Key Vault secret databricks-token)
  RECON_CATALOG               recon (prod) or recon_dev
"""

from __future__ import annotations

import json
import os
import re

from databricks import sql

MAX_ROWS = 20
CATALOG = os.getenv("RECON_CATALOG", "recon")
if not re.fullmatch(r"[A-Za-z0-9_]+", CATALOG):  # it goes into SQL text, so allow only a plain name
    raise ValueError(f"invalid RECON_CATALOG: {CATALOG!r}")

GOLD = f"{CATALOG}.gold"
SILVER = f"{CATALOG}.silver"
REPORTING = f"{CATALOG}.reporting"


def _query(statement: str, params: dict) -> list[dict]:
    with sql.connect(
        server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    ) as con, con.cursor() as cur:
        cur.execute(statement, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchmany(MAX_ROWS)]


def _dump(obj) -> str:
    return json.dumps(obj, default=str, indent=2)


def get_reconciliation_status(record_id: str) -> str:
    """Status of one payment (PAY-...) or ledger line (LED-...): matched by which rule, or which exception it sits in."""
    rid = record_id.strip().upper()
    matches = _query(
        f"""SELECT match_id, rule_id, payment_id, ledger_id, reference, currency,
                   payment_amount_cents, ledger_amount_cents, fee_cents, run_id
            FROM {GOLD}.reconciliation_matches
            WHERE payment_id = :rid OR ledger_id = :rid""",
        {"rid": rid},
    )
    if matches:
        return _dump({"record_id": rid, "status": "MATCHED", "matches": matches})
    exceptions = _query(
        f"""SELECT exception_id, reason_code, reference, payment_ids, ledger_ids,
                   payment_total_cents, ledger_total_cents, exposure_cents, status, run_id
            FROM {GOLD}.reconciliation_exceptions
            WHERE status = 'OPEN'
              AND (array_contains(split(payment_ids, ';'), :rid)
                   OR array_contains(split(ledger_ids, ';'), :rid))""",
        {"rid": rid},
    )
    if exceptions:
        return _dump({"record_id": rid, "status": "EXCEPTION", "exceptions": exceptions})
    return _dump({"record_id": rid, "status": "NOT_FOUND"})


def get_exception_details(exception_id: str) -> str:
    """Full details of one exception (EXC:REF-...), including the raw payment and ledger rows involved."""
    exc = _query(f"SELECT * FROM {GOLD}.reconciliation_exceptions WHERE exception_id = :eid",
                 {"eid": exception_id.strip()})
    if not exc:
        return _dump({"exception_id": exception_id, "status": "NOT_FOUND"})
    ref = {"ref": exc[0]["reference"]}
    return _dump({
        "exception": exc[0],
        "payments": _query(f"SELECT * EXCEPT (_source_file, _ingested_at) FROM {SILVER}.payments WHERE processor_ref = :ref", ref),
        "ledger_lines": _query(f"SELECT * EXCEPT (_source_file, _ingested_at) FROM {SILVER}.ledger WHERE reference = :ref", ref),
        "fees": _query(f"SELECT * EXCEPT (_source_file, _ingested_at) FROM {SILVER}.fees WHERE processor_ref = :ref", ref),
    })


def list_exceptions(reason_code: str = "", min_exposure_cents: int = 0) -> str:
    """Open exceptions, largest exposure first. Optionally filter by reason code and minimum exposure in cents."""
    code = reason_code.strip().upper()
    rows = _query(
        f"""SELECT exception_id, reason_code, reference, currency, exposure_cents
            FROM {GOLD}.reconciliation_exceptions
            WHERE status = 'OPEN' AND (:code = '' OR reason_code = :code)
              AND exposure_cents >= :min_exp
            ORDER BY exposure_cents DESC""",
        {"code": code, "min_exp": int(min_exposure_cents)},
    )
    return _dump(rows)


def get_kpi_snapshot(metric: str, cutoff: str, business_date: str = "2026-11-02") -> str:
    """A published KPI value. metric: transaction_rate or dollar_rate. cutoff: T+1 or T+3. Date as YYYY-MM-DD."""
    rows = _query(
        f"""SELECT business_date, cutoff, metric, value, run_id, published_at
            FROM {REPORTING}.kpi_latest
            WHERE metric = :metric AND cutoff = :cutoff AND business_date = :bd""",
        {"metric": metric.strip().lower(), "cutoff": cutoff.strip().upper(), "bd": business_date.strip()},
    )
    return _dump(rows or {"status": "NOT_FOUND"})


ALL_TOOLS = [get_reconciliation_status, get_exception_details, list_exceptions, get_kpi_snapshot]
