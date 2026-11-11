# MONOCHROME native request guard — source component only

This component does not admit the publisher, grant a network budget, deploy a collector, change a scheduler, reconcile historical traffic or enable delivery. MONOCHROME remains held until separate source-policy, owner-issued grant custody, historical request reconciliation and host qualification are accepted.

The normal native pipeline uses its existing per-source RunLockService and collector_runs ledger. A finite source/URL/time-bound admission file must match its operator-pinned SHA-256. That file/hash pair verifies bytes, not publisher permission or an owner signature. Missing, invalid, expired or exhausted admission refuses before transport. Redirect following and automatic retry are disabled for this source; unrelated collectors retain their behavior.

Before each admitted transport, the guard commits one durable charge on the native run. A persisted charged run is single-use: repeated calls, including a changed presented grant, refuse without overwriting its charge. Fresh distinct runs consume the remaining slots. Failed transport remains charged. Native MONOCHROME rows with unresolved legacy attempt metadata block new transport; this change does not migrate, erase or forgive the earlier exceeded four-request allowance. Fixture XML parsing is offline evidence, not authority to acquire that XML.

## Evidence and retained failures

The first reviewed candidate exposed a direct-call transport bypass and did not fail closed on unmetered legacy rows. Those failures were retained. A successor's bounded review passed those controls but missed repeated use of a genuine run: an independent supplement demonstrated four mocked transports under a two-attempt grant with only two durable charges. That result remains a failure of the predecessor.

The stopped successor adds current-run refresh and a charged-run refusal before reading a new grant. A causal test failed against the predecessor and passed against the repair; the same test bytes were retained. Builder affected selection: 13 passed, 280 deselected. Independent successor review: five desired-behavior observations and six focused tests passed, five deselected. These populations overlap and must not be added. A genuine first and second run made exactly two mocked transports with two charges; same-run, changed-grant reuse and a third distinct run refused. No real publisher HTTP occurred in qualification.

Review was against the exact frozen cumulative four-file component based on bf221a89740c259995f72792e4132976e2dd8fb5. Final two-file repair archive SHA-256: 1ce980ad481417895051ba89a36e0c7c1b4c26405b1e0c2c3965da829803d50d. Original RED, scoped PASS, adverse supplement, final source and terminal receipts remain in the private COPS244 campaign evidence store. No private captures are included in this publication.

## Limits

The bounded proofs use genuine native locks and runs; they are not an adversarial in-process sandbox or an independent proof of kernel lock ownership from arbitrary caller-supplied objects. Real grant custody, actual legacy-ledger reconciliation, publisher rights, natural collection, intended-host backup/recovery and delivery remain separate gates. Hosted CI must be read against the published SHA; local offline passes are not hosted CI or production acceptance. This source PR supplies no live admission configuration.
