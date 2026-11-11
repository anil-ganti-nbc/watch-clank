# PR TIMES Japanese discovery — manual editorial staging only

**Disabled by default. Not registered in production collectors, schedules, Events,
Discord, or any production newsroom queue.** `--native-staging` explicitly opts into
an isolated native lead/QC database (see below). A press appearance grants no `NEW_REFERENCE`
authority. Production activation and recurring/polling permission remain HOLD.

## Exact admitted sources

| Company | Authenticated publisher | Exact RSS URL |
|---|---|---|
| 25284 | 株式会社ウエニ貿易 | `https://prtimes.jp/companyrdf.php?company_id=25284` |
| 68154 | 株式会社 東栄 (**not 東映**) | `https://prtimes.jp/companyrdf.php?company_id=68154` |
| 46261 | スウォッチグループジャパン株式会社 | `https://prtimes.jp/companyrdf.php?company_id=46261` |

No article/detail requests, pagination guesses, alternate endpoints, policy refetch,
redirects, retries, or access to any other source are implemented.

## Offline replay (default acquisition mode)

Use the qualified project interpreter; example from the checkout:

```text
python -X utf8 -B -m app.collectors.prtimes_jp_staging --company 25284 --state-dir <owned-isolated-directory> --replay <captured-RSS1.xml> --at 2026-11-06T17:30:00+00:00
```

Repeat the identical command against the **same** state directory. The first
healthy nonempty publisher feed silently establishes its baseline, including fresh
articles. The second inserts zero articles and produces no review candidates.
Without `--replay` or `--live`, acquisition returns JSON `HOLD` and exit 2 without
a request. Success returns JSON and exit 0. `--at` is replay-only and timezone-aware.
All state lives in the explicit `prtimes-jp-staging.sqlite3`, never the application
DB. Paths containing a `data` directory and existing unmarked databases are refused.
Do not point it at production or delete/change state to replenish a request budget.

## One-shot live admission (owner-controlled)

```text
python -X utf8 -B -m app.collectors.prtimes_jp_staging --company 25284 --state-dir <same-isolated-mission-directory> --live --policy-receipt <reviewed-policy-verdicts.json> --policy-sha256 <actual-reviewed-file-sha256>
```

Use one invocation per company, sharing one mission state directory. The receipt
must bind mission `WATCH-JP-DISCOVERY-01`, the exact company/publisher, and verdict
`ACCEPT_BOUNDED_EDITORIAL_STAGING`. Its explicit SHA-256 authenticates the reviewed
bytes, **not legal permission by itself**; the operator must already have reviewed
and admitted those bytes. No live admission is inferred from source registration.
The frozen receipt is reused locally: no robots/terms requests consume the remaining
feed budget. Never create a replacement receipt merely to bypass a HOLD.

One request is durably reserved per company **for the lifetime of that state**,
including failed attempts; it is not replenished by restarting the CLI. Each live
receipt reports `requests_charged`: 1 after a durable reservation (even for 3xx,
403/429, transport failure or invalid body), 0 when refused before reservation.
This is a conservative charge, not proof that a response arrived. Offline replay
is not a live request. A 403/429
persists an origin-wide stop for all three companies. Every non-200 (including 3xx)
HOLDs without following/retrying. The client ignores ambient proxies/auth/environment,
uses TLS verification, a 20-second HTTP timeout, and a 2,000,000-byte delivered-body
cap. Shared `http_util.fetch_url` is intentionally not reused: it follows redirects
and reads ambient Settings. Current captures have no ETag/Last-Modified; no fabricated
validators or conditional-request savings are claimed. The receipt is a bounded
internal editorial-use decision, not a sustained commercial collection licence.

## Persistence, identity and editorial semantics

- RSS1 RDF only; direct namespaced `rss:item` and fields `rss:title/link/description`,
  `dc:corp/date`. Descendant media prose/channel dates never become article fields.
  UTF-8, bounded bytes/items, no DTD/entities. Missing/ambiguous publisher/title,
  wrong-company links, parse failures and unexpected empty feeds HOLD without arming.
- Article identity is its exact canonical PR TIMES publisher/release URL, scoped by
  company. Exact duplicates coalesce. Conflicting duplicates or later article edits
  HOLD the whole source before insertion. Legitimate corrections require manual
  review; this small collector does not implement enrichment/revision migration.
- Raw Japanese fields remain intact; NFKC title normalization is separate. Extra
  preorder, reissue, restock, event and preorder categories are not initial launches.
  Every classification remains review-only. Smartwatch, mixed smart/ring, ring-only
  and unknown gaming/electronic items never receive conventional-watch identities.
- A single explicitly labelled maker plus `型番`/`品番` reference can use existing
  `app.sentinel.identity.build_identity`; no free alphanumeric/character-name
  extraction. Unknown refs, multi-SKU articles and Onimusha's named three models stay
  unresolved. Non-ASCII dash variants not handled by NFKC remain unresolved rather
  than being guessed. Reference extraction is deliberately low-recall.
- Existing `classify_lead_freshness` plus `publication_timestamp_is_usable` provide
  the 72-hour boundary/future guard. Missing, date-only, naive and invalid stamps do
  not qualify. First-run silence overrides fresh publication evidence. Persisted
  observations retain baseline flag, observation clock, raw fields and review decision.
- Local maker/ref memory is shared across the three publishers. To consult existing
  cross-source Sentinel memory, explicitly supply `--known-db <existing.sqlite3>`:
  existing `SentinelStore.known_identity_keys()` is called through SQLite `mode=ro`.
  Unreadable/incompatible supplied memory fails closed. Without this option, external
  memory is **not integrated**. No unchecked `already_known` boolean is accepted.

## Verification and limits

`tests/test_prtimes_jp_staging.py` contains explicitly **synthetic** summaries of all
14 requested scenarios, plus finite parser, identity, persistence, live-denial and
real CLI checks. These are not historical captures, historical backtests, precision,
recall, or latency measurements. Parent-supplied current RSS captures are exercised
separately offline and must remain outside the repository.

## Explicit isolated native bridge

Append `--native-staging` to the replay or admitted one-shot live command. The
bridge constructs **only a brand-new** `prtimes-jp-native-staging.sqlite3` beside
the marked sidecar, with SQLite application id `731403`; existing unmarked or
symlinked native files are refused. No application DB argument, ambient database
settings, schema migration or production control registration is used. Reusing
this marked DB is permitted, never adopting an existing application DB.

Eligible conventional/watch-unresolved records use
`SpecialistLeadService.ingest_candidate`, accurately typed `ISSUER_PRESS_RELEASE`,
tier 3, always `UNCONFIRMED`. All Japanese primary attribution, classification,
raw publication timestamp and original observation clock are retained in JSON notes.
Baseline records are native `BASELINE` and absent from the native fresh QC queue.
Postbaseline fresh candidates enter that queue; existing native
`fetch_lead_queue_page`, `submit_lead_review` and `fetch_lead_history_page` APIs
read/write the isolated schema. A bridge retry uses durable sidecar evidence and
native URL dedup, not only the last acquisition result. Freshness is evaluated
again at bridge time with the canonical classifier and explicit 72-hour window.
Smart/mixed/ring/electronic records remain sidecar-only. Reissue, restock, event,
preorder and additional preorder never become `POSSIBLE_NEW_REFERENCE`.

No notifier or correlation routine is called. The canonical delivery-outcome
writer finalizes every new native lead as `gated` /
`EDITORIAL_NOTIFICATIONS_DISABLED`, irrespective of ambient delivery settings.
No CollectorRun is created, so none can be left RUNNING. Watch, SourceObservation,
ReleaseLead, Event and delivery-receipt tables remain empty in this isolated DB.

`--known-db` still opens supplied Sentinel memory in physical read-only mode;
when present, native Watch rows additionally supply exact full maker/reference
identity memory (no family matching, no mutation or official correlation).
Missing/incompatible Sentinel schema fails closed. Existing native QC reviews
are per-lead editorial feedback, **not permanent reference verdicts**; the bridge
never manufactures an operator review or treats USEFUL as publication proof.
This checkout has no newsroom-publication coverage API/table to consult. Production
newsroom coverage integration remains unavailable, not replaced by an unchecked
`already_covered` flag. Identity-less articles are deduped by exact issuer URL,
not guessed story equivalence. Do not claim cross-story dedup for unresolved refs.

`tests/test_prtimes_jp_native.py` covers the real frozen OnePiece RSS row,
clock/accessory noise, all three issuer native baselines and useful postbaseline
QC workflow, retry/CLI idempotency, smart separation, non-launch categories,
read-only Watch memory, delayed freshness and foreign native database refusal.
The real 時計ブランド reissue is retained as WATCH_UNRESOLVED, never inferred as a
conventional wristwatch. The predecessor 55-pass receipt remains historical;
new bounded receipts live exclusively in `journal/builder-integration` externally.

## Repeatable offline tests and private capture qualification

From the checkout, run all generic contracts without any private input:

```bash
unset PRTIMES_JP_CAPTURE_DIR
python -X utf8 -B -m pytest -p no:cacheprovider tests/test_prtimes_jp_staging.py tests/test_prtimes_jp_native.py tests/test_prtimes_jp_p1.py -q -rs
```

Use the qualified project interpreter in place of `python`. Without
`PRTIMES_JP_CAPTURE_DIR`, authentic-capture-dependent tests explicitly skip
with **NOT QUALIFIED**; generic scenario/native tests still run. A green
clean-host run with these skips does **not** qualify the private corpus. To
qualify it offline:

```bash
PRTIMES_JP_CAPTURE_DIR='<authorized-private-capture-directory>' python -X utf8 -B -m pytest -p no:cacheprovider tests/test_prtimes_jp_staging.py tests/test_prtimes_jp_native.py tests/test_prtimes_jp_p1.py tests/test_prtimes_jp_offline_qualification.py tests/test_prtimes_jp_coverage.py -q -rs
```

The directory must contain the owner's genuine `page-8.bin`, `page-10.bin`,
and `research/benchmark-14.json` inputs for the selected captured tests. An explicitly
configured empty/missing/unreadable path, malformed XML, wrong publisher, missing
expected article, or failed classification assertion fails, never silently skips.
No capture is downloaded, reconstructed as a purported real feed, or republished
in this repository. Keep private whole feeds external under their original access
and licensing constraints.

Classification ignores only the exact leading `[publisher_raw]` description
attribution while retaining raw evidence. A Swatch brand name alone is not a watch
noun; independent watch-primary description/title evidence still counts. A narrow
TIMEX + GMT + new-model + counted-model-release title grammar admits unresolved
editorial review without guessing references; accessory/film noise is excluded
from that alternate signal. This is not a general brand-only launch inference.

No GUI server, delivery, policy renewal, cadence, sustained collection or release
promotion is enabled or qualified. No actual production database was opened.
