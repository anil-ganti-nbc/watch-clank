# Specialist source expansion — Sprint 14

## HCC009J1 field-miss autopsy

On 2026-08-12, Monochrome's public RSS exposed `HCC009J1` at
`2026-08-12T03:00:27Z`: *Seiko Presage Classic Series x Tradman's Bonsai
Limited Edition*. Its canonical article URL is preserved in
`tests/fixtures/monochrome_hcc009j1_feed.xml`; the fixture retains only
the allowed RSS metadata and a short factual excerpt.

The local database had no Watch, SourceObservation, ReleaseLead,
SpecialistLead, or Event for the reference before onboarding. The last
Seiko product run beginning after the RSS item was `03:40:11Z`; it read the
complete current Seiko USA public Shopify catalogue (222 wrist watches) and
did not contain the reference. The corporate Seiko-news run at `03:40:09Z`
also found no relevant HCC item. This is therefore a Layer B **discovery
failure**, not parser, identity, event, or notification failure. No manual
baseline or manufactured event was performed.

## Source decision record

Approved sources are Monochrome, Deployant, Fratello and WatchTime: each
has a public bounded RSS endpoint, one request per scheduled run, and is
stored only as a `SPECIALIST_PUBLICATION` lead. All use the pre-existing
72-hour publication-time freshness policy. Missing dates remain
`UNKNOWN_TIMESTAMP`; source onboarding is `BASELINE`, never current
editorial intelligence. Existing CASIOBLOG, G-Central and Plus9Time paths
were left intact.

Hodinkee remains research-only. Its currently observed `/articles/rss`
surface is JSON rather than a verified chronological RSS contract, and no
bounded archival/delta endpoint was established in this sprint. It was not
automated rather than guessed at.

## Scheduler and cloud posture

Windows receives four independently disableable tasks. Matching systemd
units are templates only; no Hetzner host was contacted or deployed. Each
source must be run once with `--force-baseline`, then immediately once
without it and checked for zero new leads before its timer is enabled.
