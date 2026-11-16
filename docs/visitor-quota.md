# Hosted visitor allowance

The hosted shared studio limits new Google-account visitors to three successfully created tracks total, not three per day. The verified Google subject identifies the account. Email changes, signing out, browser storage changes and client-submitted role fields do not reset it. A person using several Google accounts can receive an allowance on each.

The owner and recoverable accounts present before rollout are exempt. Existing signed-out accounts that left no recoverable identity cannot be reconstructed; the owner accepted this legacy gap before rollout.

A pending job reserves one place. A durable successful terminal result consumes it. Failure or cancellation releases that place only after its authoritative terminal result, positively finished remote call and zero GPU activity are all verified. Unknown outcomes stay reserved for review. Deleting a library track does not erase its successful generation history. The account records and job reservations live in the server's durable shared store, not the browser.

The allowance is separate from the shared studio's serialized admission, credit reservation, global budget and November 2026 cutoff. Failed runs can still consume compute credits. Quota release never refunds the compute ledger. Missing or invalid policy/state blocks admission. This is not a promise of unlimited visitor availability or of no provider charges.

## Rollout

1. Keep the portfolio app link unpublished.
2. Inventory recoverable existing verified Google subjects from the current sessions/owner binding and owned jobs, without publishing those identities.
3. Insert exemption records for those subjects, then the ready policy with limit 3 and rollout timestamp. Preserve every existing guard and credit record.
4. Deploy the quota-aware adapter and backend with the exact current production settings, secrets references and privacy text; public repository values remain sanitized.
5. Verify deployed source and allowance status, and prove the fourth admission is rejected without dispatch or credit changes using isolated tests. Never generate paid music just to exercise the limit.
6. Publish the portfolio link only after those checks. Remove temporary test records and deployment credentials.

The local demonstration does not model Google enrollment or this hosted quota. `tests/test_quota.py` covers account isolation, persistence, terminal outcomes, concurrent last-slot admission, HTTP/auth bypass attempts and preservation of global guards with mocked inference.
