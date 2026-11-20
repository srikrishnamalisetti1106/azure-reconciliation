# Matching rule catalog

Rules run in this order. Each rule only sees records the earlier rules left unmatched. A match is accepted only when it is unique on both sides: the payment has exactly one candidate and the candidate has exactly one payment. Money is compared in integer cents.

## EXACT_V1 - exact match

A payment matches one ledger line when the reference (processor_ref = ledger reference), the currency and the amount in cents are all identical. Typical entry_type: SALE. If two ledger lines or two payments qualify, nothing is matched and the case becomes an exception.

## FEE_AWARE_V1 - fee-aware match

Some processors settle net of their fee, so the ledger books the net amount. A payment matches one ledger line when ledger amount = payment amount - total fees recorded in the fees file for that reference, with the same currency. Typical entry_type: SALE_NET. Fees are never estimated; only recorded fees count.

## SPLIT_V1 - split match

One payment can be booked as two or more ledger lines that share its reference (entry_type SALE_SPLIT). The lines match when their amounts add up exactly to the payment amount and exactly one unmatched payment carries that reference. A split that is off by even one cent is not matched.

## Why there is no fuzzy matching

Matching never uses tolerances, nearest amounts or guesses. Loosening rules to reduce exception counts can hide real financial breaks, so ambiguous cases always go to an analyst. Every match row stores its rule_id and run_id so any result can be traced and reproduced.
