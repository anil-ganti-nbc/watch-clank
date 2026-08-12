# Citizen regional-commercialisation autopsy — 2026-08-12

## Scope and source-of-truth note

This is a source/DB audit, not an assertion that the supplied references
are both valid. The local SQLite database and live Citizen endpoints were
checked first. Times below are UTC because that is how the local database
persists operational timestamps.

## NJ0238-57E — confirmed regional-intelligence miss

| Evidence | Result |
| --- | --- |
| Earliest first-party announcement | Citizen Watch Global, `2026-07-23`, announcement `20260723_2` |
| Announcement fact | Tsuyosa Shore limited edition, August launch scheduled, projected USD 462.50 |
| Local Watch Clank Watch | id 23, created `2026-08-11 13:22:51`, canonical `NJ0238-57E`, collection `Tsuyosa` |
| First local observation | id 10, `citizen_products`, US, `2026-08-11 13:23:28`, USD 525.00, availability unknown, baseline=true |
| Subsequent US evidence | 11 unchanged US/USD observations through `2026-08-12 05:50:48`; no event |
| UK first-party evidence | Citizen Watch UK product page lists GBP 349.00 and an Add To Basket control |
| UK local evidence | none: no UK collector and no UK observation in SQLite |
| Event / alert / GUI result | no Event existed, therefore no alert candidate or GUI official-event row existed |

The US Citizen product page was live and orderable during this audit at
USD 525.00. The global announcement had already made the reference known;
the US listing was silently absorbed into the Citizen source's initial
baseline. That baseline result was correct under the anti-historical-churn
rule, but the prior product-transition code had no path to recognize a
future first listing in another market for an already-known Watch.

Classification: `REGION_COVERAGE_GAP` (UK absent) plus
`TRANSITION_DETECTION_FAILURE` (product observations in a new region were
always treated as a first-region baseline rather than commercialisation
intelligence). It is not a parser, identity, price-comparison, alert, or
GUI-rendering failure.

## NJ0230-51E — supplied-reference discrepancy

Citizen US returned HTTP 404 for its exact official URL on 2026-08-12.
There is no `NJ0230-51E` Watch, SourceObservation, ReleaseLead, or Event in
the local database. It must not be silently mapped to a different SKU.

The source/DB evidence instead identifies **NJ0230-59L**:

| Evidence | Result |
| --- | --- |
| Local Watch Clank Watch | id 47, created `2026-08-11 13:23:28`, canonical `NJ0230-59L`, collection `Tsuyosa` |
| First US observation | id 5, `citizen_products`, USD 525.00, baseline=true |
| Later US observations | 11 unchanged US/USD observations through `2026-08-12 05:50:48`; availability unknown |
| Official US page | `NJ0230-59L`, USD 420.00 sale / USD 525.00 regular, says launched 2026-02-20 |
| Official Germany page | `NJ0230-59L`, EUR 329.00, JSON-LD declares `InStock` |

Classification for the exact supplied `NJ0230-51E`:
`EXPECTED_BY_DESIGN` (invalid/unobserved reference), not a justified
production miss. If the intended reference was `NJ0230-59L`, it shares the
regional-intelligence limitation above, but its February US launch means
it is not evidence of a new US rollout on this date.

## Regional audit

Before and after this code change, the enabled Citizen product collector is
the public US listing endpoint only. Local data contains 313 Citizen Watches
and 3,600 Citizen SourceObservations, all region `US` (302 distinct watched
references with observations). Citizen global news is a distinct `GLOBAL`
announcement source.

- US: monitored and healthy; Citizen's hydrated search listings carry
  reference, USD price, but not inventory.
- UK: not monitored. The public product page is useful first-party evidence,
  but direct Watch Clank HTTP requests returned Cloudflare 403; no collector
  was added or bypass attempted.
- Japan: not monitored. The official product page is indexed, but direct
  HTTP requests returned 403; no collector was added or bypass attempted.
- Germany/EU: not monitored. Its public product page and robots policy are
  technically readable (structured Product JSON-LD has reference, EUR price,
  and availability), but no source was added in this narrowly scoped fix:
  it needs a conservative, source-specific onboarding baseline and request-
  budget design before scheduling.

## Implemented semantics

`_record_product_transition` now considers both prior product observations
and announcement regions. After a healthy source has completed its required
baseline, a known Watch's first successful official product observation in a
new region creates `NEW_REGION`. The event includes `prior_regions`, local
price/currency, and availability when explicitly supplied. It is scored as
regional commercialisation (official first listing + first local price), not
as a price movement.

No USD/GBP or USD/EUR pair can become `PRICE_CHANGE`: the existing classifier
still requires the same region and currency. `--force-baseline` and active
epoch baselines return before regional-event creation, so an old regional
page first discovered when a source is introduced remains stored evidence,
not current news.

## Validation

Two new offline regressions cover the real pattern using `NJ0238-57E`:

1. GLOBAL announcement + US/USD observation + UK/GBP observation resolves
   one Watch and creates one high-scoring `NEW_REGION`, never a cross-currency
   `PRICE_CHANGE`; repeating UK creates no second Event.
2. The same pattern during `--force-baseline` stores the observation but
   creates no Event.

Full suite after the change: 178 passed. Ruff: clean. Two controlled live
US Citizen runs against the local DB (runs 108 and 110) were both SUCCESS
with 0 new watches and 0 events; this confirms unchanged US catalogue state
does not churn under the new logic. SQLite integrity check was `ok`; no
RUNNING rows or lock files remained.
