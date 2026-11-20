# Exception investigation SOP

Each exception groups the leftover records for one reference and has exactly one reason code. exposure_cents is the money at risk: the full amount when one side is missing, otherwise the absolute difference between the payment total and the ledger total. Work the queue from the largest exposure down.

## MISSING_IN_LEDGER

A payment was captured but nothing was booked. Check whether the ledger posting is late (it may arrive by T+3). If it is still missing after T+3, raise it with the accounting team to book the sale.

## MISSING_IN_PROCESSOR

A ledger entry has no matching processor payment. Check for a mistyped reference or a payment that was voided. Escalate to the payments team if the money was never received.

## AMOUNT_MISMATCH

One payment and one ledger line share a reference but the amounts differ and no fee explains it. Compare both source records, then ask accounting to correct the booked amount.

## FEE_MISMATCH

A processor fee is recorded, but payment minus fee still does not equal the booked net amount. Request the processor's fee statement for that reference and check for an extra or missing fee.

## SPLIT_SUM_MISMATCH

Two or more ledger lines share the payment's reference but do not add up to it. Look for a missing split line or a mistyped amount on one line.

## DUPLICATE_CANDIDATE

The same sale appears to be booked more than once, so the payment has two equally valid ledger lines. Confirm with accounting and reverse the duplicate entry. The engine never picks one on its own.

## AMBIGUOUS_MATCH

Two or more payments share the same reference, and the engine cannot tell which one a ledger line belongs to. Ask the processor to confirm the reference and match manually after review.

## What the AI assistant may and may not do

The assistant can look up records, explain reason codes and suggest next steps from this SOP. It cannot mark anything as reconciled, change amounts or close exceptions. Those actions need an authorized analyst.
