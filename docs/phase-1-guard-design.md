# Local one-attempt guard prototype

This module is not connected to the HTTP app or Modal adapter yet. It has not created any remote resources and is not a spending cap.

- A separately provisioned run-config sentinel permits one attempt during an absolute window no longer than one hour. Runtime code never creates or rearms the sentinel.
- Admission requires an active sentinel and sufficient remaining time. Atomic conditional insertion consumes the attempt once, including queue failure or cancellation.
- Terminal ownership uses a unique claim token. A separate atomic, write-once final outcome arbitrates completion, failure and cancellation. Tokens must never be returned in status responses.
- Expiry prevents new admission but does not prevent stopping or reading an already admitted job. Missing or altered sentinel identity fails closed.
- Ordinary cleanup requires confirmed failed/cancelled outcome and refuses library-indexed audio. Finalization rollback requires its claim token and must atomically record failed before removing its own incomplete publication. Completed outcomes are protected.
- The admission record must never be manually deleted, overwritten or recreated while the sentinel is valid. The store cannot distinguish first use from deleted history. Suspected alteration invalidates the run and requires a separately approved setup. This is not a tamper-proof ledger.
- Modal Dict entries expire after seven inactive days. The one-hour sentinel prevents natural expiry from rearming an active approval, but arbitrary privileged store edits are outside this guarantee.

Local verification: 84 tests pass on Python 3.10 and 3.12.15, including concurrent admission and terminal-outcome races, expiry, identity errors, cleanup preservation and symlinks. Actual Modal conditional writes, cancellation, Volume behavior and GPU execution remain untested.

Pending integration: HTTP admission/cancellation/status dependencies, call-ID retention, worker terminal fences and safe cleanup, explicit resource limits, no retries, Volume v2 and pinned SDK. No deployment or credit use is approved.

## Worker integration

The pure worker helper checks the admitted identity before model work, claims
finalization before metadata/Volume publication, and protects completed library
records from cleanup. Cancellation cleanup requires a confirmed terminal outcome.
The Modal adapter persists the FunctionCall handle internally; it is never
returned to portal clients. Polling with timeout=0 distinguishes pending calls
from observed terminal results. Unexpected exceptions do not assert termination.

The run sentinel is an operator-issued short window, not a recurring budget.
Deleting guard history or issuing a new run ID is a privileged rearm operation
and is forbidden within the one-attempt validation approval.
