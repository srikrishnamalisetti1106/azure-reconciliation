"""
Synthetic data generator for the payment-to-ledger reconciliation project.

Creates four CSV files plus a control-totals JSON in the output folder:

    payments.csv   one row per payment captured by the card processor
    ledger.csv     what accounting booked in the general ledger
    fees.csv       processor fees, used by the fee-aware rule
    expected.csv   the planted breaks and the reason code the engine MUST produce
    controls.json  row counts and money totals, for source-to-target checks

How the "good" records are built (these should all reconcile):

    EXACT      ledger amount == payment amount, same reference          (~70%)
    FEE_AWARE  ledger amount == payment amount - fee in fees.csv         (~22%)
    SPLIT      2-3 ledger lines, same reference, sum == payment amount   (~8%)

Planted breaks (one of each by default; change with --breaks-per-type):

    MISSING_IN_LEDGER     payment with no ledger line
    MISSING_IN_PROCESSOR  ledger line whose reference has no payment
    AMOUNT_MISMATCH       ledger amount is off and no fee explains it
    FEE_MISMATCH          a fee exists but doesn't explain the difference
    SPLIT_SUM_MISMATCH    split lines don't add up to the payment
    DUPLICATE_CANDIDATE   two identical ledger lines compete for one payment
    AMBIGUOUS_MATCH       two payments share a reference and amount, one ledger line

Money is always stored as integer cents (amount_cents) to avoid float rounding.
Only the Python standard library is used, and a fixed seed makes output repeatable.

Usage:
    python generate.py                       # 1,000 payments into ./output
    python generate.py --n 5000 --seed 7 --out data/synthetic/output
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

CURRENCIES = ["USD"] * 8 + ["EUR", "GBP"]  # mostly USD, a little EUR/GBP
MERCHANTS = [f"M{n:03d}" for n in range(1, 21)]
BUSINESS_DATE = datetime(2026, 11, 2, tzinfo=timezone.utc)  # the day being reconciled

BREAK_TYPES = [
    "MISSING_IN_LEDGER",
    "MISSING_IN_PROCESSOR",
    "AMOUNT_MISMATCH",
    "FEE_MISMATCH",
    "SPLIT_SUM_MISMATCH",
    "DUPLICATE_CANDIDATE",
    "AMBIGUOUS_MATCH",
]


class Generator:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.payments: list[dict] = []
        self.ledger: list[dict] = []
        self.fees: list[dict] = []
        self.expected: list[dict] = []
        self._pay_seq = 0
        self._led_seq = 0
        self._fee_seq = 0
        self._ref_seq = 0

    # ---------- id helpers ----------
    def next_payment_id(self) -> str:
        self._pay_seq += 1
        return f"PAY-{self._pay_seq:06d}"

    def next_ledger_id(self) -> str:
        self._led_seq += 1
        return f"LED-{self._led_seq:06d}"

    def next_fee_id(self) -> str:
        self._fee_seq += 1
        return f"FEE-{self._fee_seq:06d}"

    def next_ref(self) -> str:
        self._ref_seq += 1
        return f"REF-{self._ref_seq:07d}"

    # ---------- value helpers ----------
    def amount_cents(self) -> int:
        # Mostly small card payments with an occasional large one.
        if self.rng.random() < 0.05:
            return self.rng.randint(100_000, 2_500_000)  # $1,000 - $25,000
        return self.rng.randint(500, 50_000)  # $5 - $500

    def fee_for(self, amount: int) -> int:
        # Typical card pricing: 2.9% + 30 cents, rounded to whole cents.
        return round(amount * 0.029) + 30

    def event_time(self) -> str:
        t = BUSINESS_DATE + timedelta(seconds=self.rng.randint(0, 86_399))
        return t.isoformat()

    def posting_date(self) -> str:
        # Ledger posts on T+0 to T+2.
        return (BUSINESS_DATE + timedelta(days=self.rng.choice([0, 1, 1, 2]))).date().isoformat()

    # ---------- record builders ----------
    def add_payment(self, ref: str, amount: int, currency: str) -> dict:
        p = {
            "payment_id": self.next_payment_id(),
            "processor_ref": ref,
            "merchant_id": self.rng.choice(MERCHANTS),
            "amount_cents": amount,
            "currency": currency,
            "event_time": self.event_time(),
            "status": "CAPTURED",
        }
        self.payments.append(p)
        return p

    def add_ledger(self, ref: str, amount: int, currency: str, entry_type: str) -> dict:
        row = {
            "ledger_id": self.next_ledger_id(),
            "reference": ref,
            "amount_cents": amount,
            "currency": currency,
            "posting_date": self.posting_date(),
            "entry_type": entry_type,
            "account": "1010-CASH-CLEARING",
        }
        self.ledger.append(row)
        return row

    def add_fee(self, ref: str, fee: int, currency: str) -> dict:
        f = {
            "fee_id": self.next_fee_id(),
            "processor_ref": ref,
            "fee_cents": fee,
            "currency": currency,
            "fee_type": "PROCESSING",
        }
        self.fees.append(f)
        return f

    def expect(self, code: str, payment_ids: list[str], ledger_ids: list[str], note: str) -> None:
        self.expected.append(
            {
                "break_id": f"BRK-{len(self.expected) + 1:03d}",
                "expected_reason_code": code,
                "payment_ids": ";".join(payment_ids),
                "ledger_ids": ";".join(ledger_ids),
                "description": note,
            }
        )

    # ---------- good records ----------
    def good_exact(self) -> None:
        ref, amt, cur = self.next_ref(), self.amount_cents(), self.rng.choice(CURRENCIES)
        self.add_payment(ref, amt, cur)
        self.add_ledger(ref, amt, cur, "SALE")

    def good_fee_aware(self) -> None:
        ref, amt, cur = self.next_ref(), self.amount_cents(), self.rng.choice(CURRENCIES)
        fee = self.fee_for(amt)
        self.add_payment(ref, amt, cur)
        self.add_fee(ref, fee, cur)
        self.add_ledger(ref, amt - fee, cur, "SALE_NET")

    def good_split(self) -> None:
        ref, cur = self.next_ref(), self.rng.choice(CURRENCIES)
        amt = self.rng.randint(3_000, 200_000)
        parts = self.split_amount(amt, self.rng.choice([2, 3]))
        self.add_payment(ref, amt, cur)
        for part in parts:
            self.add_ledger(ref, part, cur, "SALE_SPLIT")

    def split_amount(self, amt: int, k: int) -> list[int]:
        cuts = sorted(self.rng.sample(range(100, amt - 100), k - 1))
        bounds = [0, *cuts, amt]
        return [bounds[i + 1] - bounds[i] for i in range(k)]

    # ---------- planted breaks ----------
    def break_missing_in_ledger(self) -> None:
        ref, amt, cur = self.next_ref(), self.amount_cents(), "USD"
        p = self.add_payment(ref, amt, cur)
        self.expect("MISSING_IN_LEDGER", [p["payment_id"]], [], "Payment captured but never booked in the ledger.")

    def break_missing_in_processor(self) -> None:
        ref = self.next_ref()
        led = self.add_ledger(ref, self.amount_cents(), "USD", "SALE")
        self.expect("MISSING_IN_PROCESSOR", [], [led["ledger_id"]], "Ledger entry with no matching processor payment.")

    def break_amount_mismatch(self) -> None:
        ref, amt, cur = self.next_ref(), self.amount_cents(), "USD"
        p = self.add_payment(ref, amt, cur)
        led = self.add_ledger(ref, amt + self.rng.choice([-1, 1]) * self.rng.randint(1_000, 5_000), cur, "SALE")
        self.expect("AMOUNT_MISMATCH", [p["payment_id"]], [led["ledger_id"]], "Ledger amount differs and no fee explains it.")

    def break_fee_mismatch(self) -> None:
        ref, amt, cur = self.next_ref(), self.rng.randint(10_000, 50_000), "USD"
        fee = self.fee_for(amt)
        p = self.add_payment(ref, amt, cur)
        self.add_fee(ref, fee, cur)
        led = self.add_ledger(ref, amt - fee - 250, cur, "SALE_NET")  # $2.50 unexplained
        self.expect("FEE_MISMATCH", [p["payment_id"]], [led["ledger_id"]], "Net settlement is $2.50 short of payment minus recorded fee.")

    def break_split_sum_mismatch(self) -> None:
        ref, amt, cur = self.next_ref(), self.rng.randint(20_000, 200_000), "USD"
        p = self.add_payment(ref, amt, cur)
        parts = self.split_amount(amt, 3)
        parts[-1] -= 100  # one split line is $1.00 short
        ids = [self.add_ledger(ref, part, cur, "SALE_SPLIT")["ledger_id"] for part in parts]
        self.expect("SPLIT_SUM_MISMATCH", [p["payment_id"]], ids, "Three split lines sum to $1.00 less than the payment.")

    def break_duplicate_candidate(self) -> None:
        ref, amt, cur = self.next_ref(), self.amount_cents(), "USD"
        p = self.add_payment(ref, amt, cur)
        a = self.add_ledger(ref, amt, cur, "SALE")
        b = self.add_ledger(ref, amt, cur, "SALE")  # booked twice
        self.expect("DUPLICATE_CANDIDATE", [p["payment_id"]], [a["ledger_id"], b["ledger_id"]], "Same sale booked twice in the ledger.")

    def break_ambiguous_match(self) -> None:
        ref, amt, cur = self.next_ref(), self.amount_cents(), "USD"
        p1 = self.add_payment(ref, amt, cur)
        p2 = self.add_payment(ref, amt, cur)  # processor reused the reference
        led = self.add_ledger(ref, amt, cur, "SALE")
        self.expect("AMBIGUOUS_MATCH", [p1["payment_id"], p2["payment_id"]], [led["ledger_id"]], "Two payments share reference and amount; one ledger line.")

    # ---------- orchestration ----------
    def build(self, n_payments: int, breaks_per_type: int) -> None:
        breakers = {
            "MISSING_IN_LEDGER": self.break_missing_in_ledger,
            "MISSING_IN_PROCESSOR": self.break_missing_in_processor,
            "AMOUNT_MISMATCH": self.break_amount_mismatch,
            "FEE_MISMATCH": self.break_fee_mismatch,
            "SPLIT_SUM_MISMATCH": self.break_split_sum_mismatch,
            "DUPLICATE_CANDIDATE": self.break_duplicate_candidate,
            "AMBIGUOUS_MATCH": self.break_ambiguous_match,
        }
        for code in BREAK_TYPES:
            for _ in range(breaks_per_type):
                breakers[code]()

        while len(self.payments) < n_payments:
            r = self.rng.random()
            if r < 0.70:
                self.good_exact()
            elif r < 0.92:
                self.good_fee_aware()
            else:
                self.good_split()

        # Shuffle so planted breaks aren't all at the top of the files.
        self.rng.shuffle(self.payments)
        self.rng.shuffle(self.ledger)
        self.rng.shuffle(self.fees)

    def controls(self) -> dict:
        def totals(rows: list[dict], key: str) -> dict:
            out: dict[str, int] = {}
            for r in rows:
                out[r["currency"]] = out.get(r["currency"], 0) + r[key]
            return dict(sorted(out.items()))

        return {
            "business_date": BUSINESS_DATE.date().isoformat(),
            "row_counts": {
                "payments": len(self.payments),
                "ledger": len(self.ledger),
                "fees": len(self.fees),
                "expected_breaks": len(self.expected),
            },
            "amount_cents_by_currency": {
                "payments": totals(self.payments, "amount_cents"),
                "ledger": totals(self.ledger, "amount_cents"),
                "fees": totals(self.fees, "fee_cents"),
            },
        }


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic reconciliation data.")
    parser.add_argument("--n", type=int, default=1000, help="number of payments (default 1000)")
    parser.add_argument("--seed", type=int, default=42, help="random seed for repeatable output")
    parser.add_argument("--breaks-per-type", type=int, default=1, help="planted breaks of each type")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "output", help="output folder")
    args = parser.parse_args()

    gen = Generator(args.seed)
    gen.build(args.n, args.breaks_per_type)

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "payments.csv", gen.payments)
    write_csv(args.out / "ledger.csv", gen.ledger)
    write_csv(args.out / "fees.csv", gen.fees)
    write_csv(args.out / "expected.csv", gen.expected)
    controls = gen.controls()
    (args.out / "controls.json").write_text(json.dumps(controls, indent=2), encoding="utf-8")

    c = controls["row_counts"]
    print(f"Wrote {c['payments']} payments, {c['ledger']} ledger lines, {c['fees']} fees, "
          f"{c['expected_breaks']} planted breaks to {args.out}")


if __name__ == "__main__":
    main()
