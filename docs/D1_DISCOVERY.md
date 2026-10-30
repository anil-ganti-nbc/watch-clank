# Manual D1 Discovery — Watch Clank

Discovery is a separate, manual proposal inbox for traditional analogue and digital
watches. It is not catalogue admission, manufacturer attribution, novelty proof,
notification delivery, or a collector schedule. Smartwatch Clank and Grok state are
not inputs. Existing read-only/local-operator launch semantics are unchanged.

## Producer prerequisites and binding

Supply external absolute paths to the accepted **original** `d1.py` and
`profiles.json`; neither is packaged here. Accepted content identity:
`5e138fabb1918e2fec8a570348c4a69070a4b00bdc2d488dea86d07ac45ad118`.
Executable SHA256: `eaac15eb9ad414b2bbdd63870b70bbc1a8b2d93d9f21f1308c43db3217c69bf1`.
Original profiles SHA256: `a2ba255264a5599ac717b2c3a464e34c7f1a32564e55ca6949937afa5aa1375e`.

The original has computing and watches profiles. Refresh authenticates both files,
projects only the exact watches object into owned refresh staging, validates that
projection through the unchanged D1 `load_config`, and executes unchanged D1 with
`run --config PROJECTION --state OWNED_STAGE`. Destination is
`watches-clank:proposal-review`; the sole accepted source is
`https://monochrome-watches.com/feed/` (MONOCHROME Watches). No computing fetch,
new endpoint, modified producer regex, or manufacturer/model resolution is added.

The thin store/DTO/prepare and dashboard mechanisms derive from Tablet merge
`b0fd78db347e9254dd79a8f9dc4813fbff6a1056` and OEM Radar merge
`f5a2379e0a2953a471850785648b7e058970cb2a`; neither is a runtime dependency.
Consumer-only title exclusions live in `config/discovery.routing.v1.json`.
Standalone accessories use a configuration-owned primary-subject heuristic: ignore
trailing attribute phrases such as "with a steel bracelet", then exclude an
accessory occurring before any watch subject clue. A watch-primary introduction
is not rejected merely for an included strap/bracelet. This bounded title rule is
not a semantic classifier and may be ambiguous for unusual word order.
These conservative exclusions are not relevance qualification results. They
exclude negatives rather than relabelling them as research. The source payload
and producer review state are not rewritten.

## Explicit operations

Use the project Python 3.12 environment. Every command requires an **absolute**
settings path. `config/discovery.example.json` is disabled by default; adapt its
external paths, never point it at canonical Watch data or a legacy database.
Keep producer evidence and consumer data in separate dedicated roots.

```text
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json configure --producer-id watch-d1-v1 --producer C:/D1/accepted/d1.py --profiles C:/D1/accepted/profiles.json --evidence-root C:/WatchDiscovery/producer-evidence --data-dir C:/WatchDiscovery/consumer
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json enable
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json import C:/WatchDiscovery/export.jsonl
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json list
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json show LEAD_ID
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json review LEAD_ID --label editorial_keep
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json disable
```

Only the explicit `refresh` operation collects; do not use it without separate
operator collection authorization. No scheduler, model call, delivery, migration,
or legacy collector is invoked by these modules. Original D1 supports no limit
flags: its single projected source makes one bounded fetch, with at most three
redirect requests, a 5MiB response and 8-second fetch subprocess. The adapter
also enforces a 90-second subprocess deadline and refuses unknown/excess
completion accounting (6 requests / 10MiB maximum). Every refresh keeps its
projection/hash, argv, stdout, stderr, exit and parsed report in owned staging,
including failures. Two retained manual qualification runs each made one actual
HTTP request (20,781 bytes) and retained the same eight proposals (seven main,
one research). These are not eight verified new launches. No further publisher
requests are authorised for the closeout described below.

## View only (operator launch, not part of testing)

```text
python -m app.serve --discovery-only --settings C:/WatchDiscovery/settings.json
python -m app.discovery_cli --settings C:/WatchDiscovery/settings.json serve --port 8790
```

Discovery-only branches before legacy Settings, app.main, database initialization,
and collector imports. It binds only 127.0.0.1, defaults to 8790, rejects 8765,
and never opens a browser or stops another listener. Viewing is GET-only and
cannot collect/import/review/deliver. All retained leads are rendered without a
cap, alongside main/research/unresolved counts, source links, literal passages,
separate source/producer/import dates, limitations and observation/review history.
Copied evidence is hash-checked, plain text, nosniff, and CSP-sandboxed.

## Persistence, replay, disable and rollback

Stable keys are `(watch-d1-v1, original D1 lead_id)`. Identical imports retain
reviews without another observation; changed observations append history;
omissions do not delete proposals. Full validation precedes publication. A
`CURRENT` atomic pointer publishes a matching SQLite sidecar and JSONL generation.
Failed validation or refresh leaves the previous generation usable. Missing
referenced evidence is explicitly `unavailable/quarantined`; verified blobs are
bounded and copied into the consumer's persistent content-addressed store.
Old observation blobs and consumer review histories remain inspectable.

Disable prevents refresh/import/review but preserves reading and all data/history.
Stop only the Discovery process to roll back its view. No legacy runtime changes
are required. Operate with one explicit manual writer at a time; multiwriter
locking and aggregate lifetime storage quotas are not provided in this submission.

## Offline tests and limits

```text
python -m pytest tests/test_d1_watch_discovery.py -q
python -m pytest -m "not live"
python -m ruff check app/discovery.py app/discovery_cli.py app/discovery_dashboard.py app/serve.py tests/test_d1_watch_discovery.py
```

Generic fixtures are explicitly synthetic and test store/presentation mechanics.
The external accepted-D1 test reads `WATCH_CLANK_D1_ROOT` containing `d1.py` and
original `profiles.json`. Only an absent `WATCH_CLANK_D1_ROOT` explicitly skips
external qualification. A supplied empty/relative/nonexistent root or missing,
non-file, or invalid selected producer/profile fails; authentication is never
waived by a valid directory. Tests use synthetic RSS, not private captured articles.
Tests deny network; campaign-wide baseline uses the parent's inherited guard.
The source-backed development set recovered seven of eight relevant documents
(all seven main), with zero of eight negatives in either main or research. Labels
remain unchanged; the Glashütte Senator miss is retained. Four negative specimens
use authentic search-card headlines/bylines/dates rather than article prose.
Retained other-publisher controls qualify relevance, not new Watch sources. The
finite observed feed window and these exposed examples do not prove exhaustive
watch coverage or global novelty. Independent review and local activation remain
separate acceptance steps, not consequences of a green portable test job.

## Disclosed acquisition exception and accounting scope

The initial one-off source-check tooling counted **four fetch operations**, but
implicit redirects meant **at least six HTTP requests against a cap of four**.
Exact intermediate counts were not recorded and remain unknown. That acquisition
was not compliant. Its non-qualified interactive driver is retired/disabled; it
is not part of this delivered application or a supported operating path.

A subsequent explicit owner exception permits retained-evidence closeout, review,
source publication and conditional local manual activation without further
publisher acquisition. It does not raise the historical cap, reset counters,
reconstruct receipts or make the earlier acquisition compliant. The two later
live-validation runs retain their own independent wire counts.

The delivered refresh path uses the unchanged authenticated D1 transport instead
of urllib's uncounted redirect following. Its fetch worker consumes allowance
before each attempted HTTP request, including redirect hops and failed attempts;
exhaustion refuses the next attempt. The original watch-only projection has one
source and one fetch, with a structural maximum of three redirect attempts below
the adapter's six-request allowance; the adapter does not retry collection.
`tests/test_d1_request_accounting.py` uses mocked connections for redirect,
exhaustion, explicit retry/failure, child-count aggregation and unknown-on-timeout
contrasts. These offline tests neither contact publishers nor reconstruct the
missing historical request sequence. No delivered executable/profile change was
needed for the one-off counting defect. Private capture-backed qualification and
existing composed-run evidence are separate from portable CI and explicit skips.
