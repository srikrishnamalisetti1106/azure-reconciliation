"""
Read-only lookup tools for the investigation assistant.

Every tool runs a FIXED, parameterized SQL query against local/recon.duckdb,
opened in read-only mode. The AI can choose which tool to call and pass IDs,
but it can never write SQL or change data. That is the core safety design.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

DB = Path(__file__).resolve().parents[1] / "local/recon.duckdb"
MAX_ROWS = 20


def _query(sql: str, params: list) -> list[dict]:
    with duckdb.connect(str(DB), read_only=True) as con:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchmany(MAX_ROWS)]


def _dump(obj) -> str:
    return json.dumps(obj, default=str, indent=2)


def get_reconciliation_status(record_id: str) -> str:
    """Status of one payment (PAY-...) or ledger line (LED-...): matched by which rule, or which exception it sits in."""
    record_id = record_id.strip().upper()
    matches = _query(
        """SELECT match_id, rule_id, payment_id, ledger_id, reference, currency,
                  payment_amount_cents, ledger_amount_cents, fee_cents, run_id
           FROM reconciliation_matches WHERE payment_id = ? OR ledger_id = ?""",
        [record_id, record_id],
    )
    if matches:
        return _dump({"record_id": record_id, "status": "MATCHED", "matches": matches})
    exceptions = _query(
        """SELECT exception_id, reason_code, reference, payment_ids, ledger_ids,
                  payment_total_cents, ledger_total_cents, exposure_cents, run_id
           FROM reconciliation_exceptions
           WHERE list_contains(string_split(payment_ids, ';'), ?)
              OR list_contains(string_split(ledger_ids, ';'), ?)""",
        [record_id, record_id],
    )
    if exceptions:
        return _dump({"record_id": record_id, "status": "EXCEPTION", "exceptions": exceptions})
    return _dump({"record_id": record_id, "status": "NOT_FOUND"})


def get_exception_details(exception_id: str) -> str:
    """Full details of one exception (EXC:REF-...), including the raw payment and ledger rows involved."""
    exc = _query("SELECT * FROM reconciliation_exceptions WHERE exception_id = ?", [exception_id.strip()])
    if not exc:
        return _dump({"exception_id": exception_id, "status": "NOT_FOUND"})
    ref = exc[0]["reference"]
    return _dump({
        "exception": exc[0],
        "payments": _query("SELECT * FROM payments WHERE processor_ref = ?", [ref]),
        "ledger_lines": _query("SELECT * FROM ledger WHERE reference = ?", [ref]),
        "fees": _query("SELECT * FROM fees WHERE processor_ref = ?", [ref]),
    })


def list_exceptions(reason_code: str = "", min_exposure_cents: int = 0) -> str:
    """Open exceptions, largest exposure first. Optionally filter by reason code and minimum exposure in cents."""
    rows = _query(
        """SELECT exception_id, reason_code, reference, currency, exposure_cents
           FROM reconciliation_exceptions
           WHERE (? = '' OR reason_code = ?) AND exposure_cents >= ?
           ORDER BY exposure_cents DESC""",
        [reason_code.upper(), reason_code.upper(), int(min_exposure_cents)],
    )
    return _dump(rows)


def get_kpi_snapshot(metric: str, cutoff: str, business_date: str = "2026-11-02") -> str:
    """A published KPI value. metric: transaction_rate or dollar_rate. cutoff: T+1 or T+3. Date as YYYY-MM-DD."""
    rows = _query(
        "SELECT * FROM kpi_snapshots WHERE metric = ? AND cutoff = ? AND business_date = ?",
        [metric.strip().lower(), cutoff.strip().upper(), business_date.strip()],
    )
    return _dump(rows or {"status": "NOT_FOUND"})


ALL_TOOLS = [get_reconciliation_status, get_exception_details, list_exceptions, get_kpi_snapshot]
