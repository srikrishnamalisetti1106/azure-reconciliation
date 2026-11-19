# KPI definitions

Both KPIs are calculated per business date over all captured payments, at two cutoffs. Published snapshots are append-only and are never overwritten.

## transaction_rate - transaction reconciliation rate

100 x matched payments / all payments. Answers: what share of transactions reconciled? Every payment counts the same, whatever its size.

## dollar_rate - dollar reconciliation rate

100 x cents in matched payments / cents in all payments (gross payment amounts). Answers: what share of the money reconciled? A single large unmatched payment lowers this much more than the transaction rate.

## Cutoffs: T+1 and T+3

T+1 is the preliminary snapshot, one day after the business date, using only ledger lines posted by then. T+3 is the final snapshot. Rates usually rise from T+1 to T+3 because some ledger lines post one or two days late. A low T+1 rate alone is not a problem; a low T+3 rate is.

## Restatements

If late data changes a number after it was published, the original snapshot stays as reported and a new snapshot is added with a later snapshot_at time. Always say which cutoff and which snapshot a number comes from.
