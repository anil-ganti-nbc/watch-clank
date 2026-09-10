"""Horology Sentinel: fast, cheap, deterministic first-stage tripwire.

Detects previously unseen watch identities on first-party sources and
sends an immediate Discord WATCH SIGHTING. It is NOT the editorial
pipeline: no enrichment, no novelty verdict, no article-worthiness call --
"A previously unseen watch identity or relevant first-party URL has
appeared in a monitored source" is the entire claim a sighting makes.
`first_seen != market novelty` (WATCH_EVENT_SEMANTICS.md) is preserved by
construction: the Sentinel creates no Events at all.

Layout:
- config.py    declarative source registry (brand N+1 is a config entry)
- identity.py  reference extraction/normalization + global identity keys
- adapters.py  cheap discovery adapters (Shopify products.json, sitemap XML)
- store.py     known-identity store + per-source baseline arming
- alerting.py  WATCH SIGHTING formatting + Discord delivery + receipts
- runner.py    sweep orchestration (lock, per-source isolation, run stats)

Reuses fleet infrastructure deliberately: app.collectors.http_util for
HTTP, app.normalization.references for brand normalization, RunLockService
for overlap protection, DiscordNotifier + DeliveryReceiptService for
delivery, CollectorRun for run records, Alembic for persistence.
"""
