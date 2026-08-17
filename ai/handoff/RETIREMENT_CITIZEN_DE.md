# citizen_de retirement (2026-08-17)

## What happened

Per explicit owner directive, issued as part of the 2026-08-17 production-
state audit/clean-baseline-reset operation: `citizen_de` (Citizen Germany
product/catalogue observation) is retired from production permanently.
Reason given: it has proven sufficiently problematic/noisy that it should
no longer be relied on. This document records exactly what was changed
and why, per that operation's explicit "document exactly how it was
retired" requirement.

## What `citizen_de` was

- **Collector:** `app/collectors/citizen_de_products.py`
  (`CitizenGermanyProductsCollector`, `COLLECTOR_ID = "citizen_de_products"`)
  — sitemap-delta discovery of Citizen's German product catalogue.
- **Parser:** `app/parsers/citizen_de_products.py`
  (`parse_citizen_de_product_html`).
- **Pipeline registration:** an entry in
  `PipelineService._PRODUCT_REGISTRY["citizen_de"]`
  (`app/services/pipeline.py`), reachable via
  `run_product_observation_pipeline("citizen_de", ...)`.
- **Production allowlist:** `"citizen_de_products"` in
  `KNOWN_COLLECTORS` (`app/services/health.py`) and a matching
  `CollectorControl` entry in `_CONTROLS`
  (`app/services/collector_registry.py`) — together these drove
  `SAFE_COLLECTOR_IDS`, the dashboard's `/operations` RUN NOW list, and
  the `/operations/run-all-safe` "RUN ALL SAFE COLLECTORS" button.
- **Health monitoring:** `"citizen_de_products": 720` in
  `EXPECTED_CADENCE_MINUTES` (12h expected cadence).
- **CLI:** `"citizen_de"` in `scripts/run_pipeline.py`'s
  `--experimental-product` choices.
- **Scheduling:** a Windows Task Scheduler lane
  (`scripts/install_windows_experimental_tasks.ps1`,
  `scripts/run_scheduled_experimental.ps1`) and a bare-metal systemd unit
  pair (`scripts/systemd/watch-clank-citizen-de-products.{service,timer}`)
  — the latter unused by Hetzner (which runs the separate Docker/
  `render_units.py` path, generated live from `collector_registry.py`,
  not from checked-in unit files).
- **Tests/fixtures:** `test_citizen_de_product_parser_uses_first_party_jsonld`,
  `test_citizen_de_sitemap_discovery_is_bounded_and_skips_known_urls`,
  `test_citizen_de_products_force_baseline_then_repeat_is_silent`,
  `test_citizen_de_first_normal_listing_of_known_us_reference_emits_new_region`
  (`tests/test_core.py`) — all exercise the collector/parser/pipeline
  mechanism directly via the Python API, not the CLI/registry.

## How it was retired

Per the project's own convention observed elsewhere in this codebase (no
explicit "deprecated" flag exists anywhere -- production reachability is
entirely determined by presence in `KNOWN_COLLECTORS`/`_CONTROLS`/the CLI
choices list): **removed from every production-facing registry and
surface, left otherwise intact.**

Changed:
1. `app/services/health.py` — removed from `KNOWN_COLLECTORS` and
   `EXPECTED_CADENCE_MINUTES`.
2. `app/services/collector_registry.py` — removed the `CollectorControl`
   entry (the module's own import-time sanity check, which cross-
   validates `KNOWN_COLLECTORS` against `_CONTROLS`, confirms these two
   changes stay in sync).
3. `scripts/run_pipeline.py` — removed `"citizen_de"` from
   `--experimental-product`'s `choices`, so it is rejected at the CLI
   argument-parsing layer, not merely absent from the registry.
4. `scripts/install_windows_experimental_tasks.ps1` /
   `scripts/run_scheduled_experimental.ps1` — removed the Windows lane
   entirely (not commented out) so re-running the installer cannot
   silently reinstall it.
5. `scripts/systemd/watch-clank-citizen-de-products.{service,timer}` —
   deleted (unused bare-metal templates; Hetzner's actual Docker units
   are generated live from `collector_registry.py`, so step 2 alone
   already prevents any future regeneration from including it).
6. `scripts/systemd/README.md` / `scripts/systemd/docker/README.md` —
   removed the now-invalid example onboarding command line.

Deliberately NOT changed:
- The collector and parser implementation files themselves.
- `PipelineService._PRODUCT_REGISTRY["citizen_de"]` and
  `run_product_observation_pipeline`'s acceptance of `brand="citizen_de"`
  via direct Python calls — this keeps the mechanism testable and
  available for future manual archaeology, while being unreachable from
  any production entrypoint (CLI, dashboard, scheduler) as of this
  change.
- The 4 existing tests listed above — all still pass unchanged, proving
  the underlying implementation's correctness is preserved, only its
  production reachability was removed.
- Any historical `citizen_de_products` rows already in any database
  (`collector_runs`, `source_observations`, `events`, etc.) — untouched,
  preserved as historical evidence, per the operation's explicit
  instruction not to destructively rewrite archived history.

## New guard test

`test_citizen_de_products_is_retired_from_the_active_production_source_set`
(`tests/test_core.py`) asserts directly against `KNOWN_COLLECTORS`,
`SAFE_COLLECTOR_IDS`, `EXPECTED_CADENCE_MINUTES`, `all_controls()`, and
the real CLI subprocess's argparse rejection — so `citizen_de_products`
cannot silently reappear even if one of these lists is rebuilt from a
stale copy/paste in the future.

## What was NOT touched (Hetzner freeze)

Hetzner already has a live, running `citizen_de_products` systemd timer
(installed during the 2026-08-14/15 redeployment sprint, per
`ai/handoff/HETZNER_DEPLOYMENT.md`). Per this operation's explicit
"HETZNER FREEZE" instruction, **Hetzner was not accessed, modified, or
redeployed as part of this retirement.** Its `citizen_de_products` timer
continues running exactly as before and will keep doing so until a
future, separately-authorized Hetzner redeploy picks up this change
(at which point `render_units.py`, generating from the now-updated
`collector_registry.py`, will naturally omit it).

## What was NOT verified (Windows)

Windows was unreachable this session (a standing condition carried from
prior sprints, not specific to this operation). If a
`WatchClank-CitizenGermanyProducts` scheduled task is currently installed
there, this change does not remove it — the installer script no longer
offers to (re)install it, but does not uninstall an existing one either.
This is a deferred, not-yet-verified item, not assumed to already be
handled.

## Fresh production DB (this same operation)

The new baseline established as part of this same 2026-08-17 operation
deliberately excludes `citizen_de` from the baseline crawl — see
`ai/handoff/PRODUCTION_RESET_20260817.md` for the full active source list
verified after the reset.
