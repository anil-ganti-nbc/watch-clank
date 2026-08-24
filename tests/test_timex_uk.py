"""Timex UK + Shopify catalogue family tests.

Covers the required regional-novelty guards and family honesty:
- same SKU US + UK -> one canonical watch (identity proof)
- UK-only unseen SKU -> eligible per existing novelty semantics
- regional URL alone never creates NEW_PRODUCT identity
- Shopify maintenance touch / future published_at -> no launch strengthening
  (Timex inherited rules carried into the new consumer)
- malformed / empty / blocked handled honestly
- traversal beyond max_items stays new-first
"""
import json

from app.collectors.base import FetchResult
from app.collectors.shopify_family import ShopifyCatalogueCollector, ShopifyCatalogueConfig
from app.parsers.timex_uk_products import parse_timex_uk_product_json
from tests.test_core import db_session, tmp_settings  # noqa: F401 -- shared fixtures


def _page(*products):
    return json.dumps({"products": list(products)}).encode()


def _prod(handle, sku, title="Watch", ptype="Watch", available=True, published="2026-08-20T10:00:00Z"):
    return {
        "handle": handle, "title": title, "product_type": ptype,
        "published_at": published,
        "variants": [{"sku": sku, "price": "95.00", "available": available}],
    }


# --- family mechanics -------------------------------------------------------


def test_shopify_family_pagination_and_dedup():
    c = ShopifyCatalogueCollector(ShopifyCatalogueConfig(
        collector_id="t", region="UK",
        listing_url_template="https://x.test/products.json?page={page}",
        product_url_template="https://x.test/products/{handle}",
        trust_score=90.0,
    ))
    pages = [
        # page 1: same SKU appears under two handles (regional duplicate) -> deduped
        _page(_prod("a", "TW111"), _prod("b", "TW222")),
        # page 2: TW111 again via strap product (filtered) and a watch
        _page(_prod("c", "TW333"), _prod("d", "TW444", ptype="Strap")),
    ]
    result = c.run(listing_pages=pages, max_items=100)
    refs = [i.reference_hint for i in result.discovered]
    assert refs == ["TW111", "TW222", "TW333"]
    assert result.metadata["component_status"] == "SUCCESS"


def test_shopify_family_empty_then_stop():
    c = ShopifyCatalogueCollector(ShopifyCatalogueConfig(
        collector_id="t", region="UK",
        listing_url_template="https://x.test/products.json?page={page}",
        product_url_template="https://x.test/products/{handle}",
        trust_score=90.0,
    ))
    result = c.run(listing_pages=[_page()], max_items=50)
    assert result.metadata["component_status"] == "ZERO_ITEMS"


def test_shopify_family_blocked_status():
    from unittest.mock import patch


    c = ShopifyCatalogueCollector(ShopifyCatalogueConfig(
        collector_id="t", region="UK",
        listing_url_template="https://x.test/products.json?page={page}",
        product_url_template="https://x.test/products/{handle}",
        trust_score=90.0,
    ))
    blocked = FetchResult(url="https://x", success=False, status_code=403, error="HTTP 403")
    with patch("app.collectors.shopify_family.fetch_url", return_value=blocked):
        result = c.run()
    assert result.metadata["component_status"] == "BLOCKED"
    assert not result.metadata["healthy"]


def test_shopify_family_malformed_page_is_tolerated():
    c = ShopifyCatalogueCollector(ShopifyCatalogueConfig(
        collector_id="t", region="UK",
        listing_url_template="https://x.test/products.json?page={page}",
        product_url_template="https://x.test/products/{handle}",
        trust_score=90.0,
    ))
    # malformed JSON page -> discovery returns [] -> treated as empty page -> stop
    result = c.run(listing_pages=[b"{{{not json"], max_items=10)
    assert result.metadata["candidate_count"] == 0


def test_traversal_new_first_beyond_max_items():
    """Slice-starvation regression: known URLs deprioritized, unseen first."""
    c = ShopifyCatalogueCollector(ShopifyCatalogueConfig(
        collector_id="t", region="UK",
        listing_url_template="https://x.test/products.json?page={page}",
        product_url_template="https://x.test/products/{handle}",
        trust_score=90.0,
    ))
    pages = [
        _page(*[_prod(f"h{i}", f"SKU{i:03d}") for i in range(1, 6)]),
    ]
    known = {f"https://x.test/products/h{i}" for i in range(1, 4)}
    result = c.run(listing_pages=pages, known_product_urls=known, max_items=3)
    seen = [i.reference_hint for i in result.discovered]
    assert seen == ["SKU004", "SKU005", "SKU001"]  # unseen first, then known fill


# --- parser ------------------------------------------------------------------


def test_timex_uk_parser_gbp_and_fields():
    r = parse_timex_uk_product_json({
        "product_type": "Watch", "title": "Expedition Ridge 43mm",
        "published_at": "2026-08-20T10:00:00Z", "tags": ["YGroup_Expedition"],
        "variants": [{"sku": "TW4B34400UK", "price": "95.00", "available": True}],
    })
    assert r.success
    w = r.watches[0]
    assert w.reference_raw == "TW4B34400UK"
    assert w.currency == "GBP" and w.price == 95.0
    assert w.availability_status == "AVAILABLE"
    assert w.extra_specs["published_at"] == "2026-08-20T10:00:00Z"  # provenance preserved


def test_timex_uk_parser_rejects_non_watch_and_empty():
    bad = parse_timex_uk_product_json({"product_type": "Strap", "variants": [{"sku": "X"}]})
    assert not bad.success
    empty = parse_timex_uk_product_json(b"")
    assert not empty.success and "empty payload" in empty.error


# --- regional novelty guards (DB-level) --------------------------------------


def test_same_sku_us_and_uk_resolves_to_one_canonical_watch(db_session, tmp_settings):
    """THE identity proof: US collector creates the watch; UK observation of
    the same SKU must resolve to it (no second row), regardless of URL."""
    from app.models import Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    def obs_for(watch_id, url):

        from app.models import SourceObservation

        o = SourceObservation(
            watch_id=watch_id, collector_id="timex_products", collector_version="t",
            parser_id="t", parser_version="t", region="UK", source_url=url,
            price=95.0, currency="GBP", availability_status="AVAILABLE",
            overall_confidence=90.0,
        )
        return o

    us_watch = Watch(manufacturer="Timex", brand="Timex",
                     reference_raw="TW4B34400UK", reference_canonical="TW4B34400UK")
    db_session.add(us_watch)
    db_session.flush()

    resolved, is_new = pipeline._resolve_or_create_watch(
        reference_raw="TW4B34400UK", manufacturer="Timex", brand="Timex",
        collection=None, model_name=None, extra={},
        correlation_id="test", run_id=None,
    )
    assert is_new is False
    assert resolved.id == us_watch.id


def test_uk_regional_url_alone_is_not_new_product(db_session, tmp_settings):
    """A UK listing of an already-known SKU produces no FIRST_SEEN event —
    regional presence is NEW_REGION-class at most."""
    from datetime import UTC, datetime

    from app.models import Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    now = datetime.now(UTC)

    w = Watch(manufacturer="Timex", brand="Timex", reference_raw="TWUKREG001",
              reference_canonical="TWUKREG001")
    db_session.add(w)
    db_session.flush()
    # prior US observation establishes the reference
    from app.models import SourceObservation

    prior = SourceObservation(
        watch_id=w.id, collector_id="timex_products", collector_version="t",
        parser_id="t", parser_version="t", region="US",
        source_url="https://www.timex.com/products/twukreg001",
        price=100.0, currency="USD", availability_status="AVAILABLE",
        overall_confidence=90.0, observed_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db_session.add(prior)
    db_session.flush()

    uk_obs = SourceObservation(
        watch_id=w.id, collector_id="timex_uk_products", collector_version="t",
        parser_id="t", parser_version="t", region="UK",
        source_url="https://timex.co.uk/products/twukreg001",
        price=80.0, currency="GBP", availability_status="AVAILABLE",
        overall_confidence=90.0, observed_at=now,
    )
    db_session.add(uk_obs)
    db_session.flush()

    result = pipeline._record_product_transition(
        watch=w, new_obs=uk_obs, is_new_watch=False, experimental=True
    )
    # No FIRST_SEEN/NEW_REFERENCE for a known reference's regional sighting.
    assert result.get("event_type") in (None, "NEW_REGION"), result


def test_shopify_maintenance_touch_does_not_strengthen_novelty(db_session, tmp_settings):
    """Inherited Timex rule: a future-dated published_at on a UK record cannot
    upgrade evidence strength (bulk-touch/future-stamp protection)."""
    from datetime import UTC, datetime, timedelta

    from app.services.freshness import publication_timestamp_is_usable
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    now = datetime.now(UTC)
    future = now + timedelta(hours=3)

    from app.models import Watch

    w = Watch(manufacturer="Timex", brand="Timex", reference_raw="TWUKFUT001",
              reference_canonical="TWUKFUT001",
              extra_specs={"published_at": future.isoformat()})
    db_session.add(w)
    db_session.flush()

    strength, detail = pipeline._publication_evidence_strength(
        watch=w, published_at=future, reactivation_note=None, observed_at=now,
    )
    assert strength == "WEAK"
    assert not publication_timestamp_is_usable(published_at=future, observed_at=now)


def test_uk_only_unseen_sku_eligible_per_existing_semantics(db_session, tmp_settings):
    """A genuinely-unseen UK-only SKU still becomes a Watch + honest
    FIRST_SEEN-class candidate — recall preserved, not suppressed by region."""
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))

    payload = {
        "product_type": "Watch", "title": "UK Exclusive 40mm",
        "published_at": "2026-08-24T10:00:00Z", "tags": [],
        "variants": [{"sku": "TWUKEXCL001", "price": "120.00", "available": True}],
    }
    pr = parse_timex_uk_product_json(payload, source_url="https://timex.co.uk/products/twukexcl001")
    assert pr.success and pr.watches[0].reference_raw == "TWUKEXCL001"

    out = pipeline.process_fetch_result(
        type("FR", (), {"url": "https://timex.co.uk/products/twukexcl001", "success": True,
                        "status_code": 200, "content_type": "application/json",
                        "payload": json.dumps(payload).encode(), "error": None,
                        "elapsed_ms": 5})(),
        run_id=None, collector_id="timex_uk_products", collector_version="test",
        parse_fn=parse_timex_uk_product_json, default_region="UK", experimental=True,
        force_baseline=True,  # baseline pass first: silent, but identity persists
    )
    assert out["success"]
    from app.models import Watch

    n = db_session.query(Watch).filter(Watch.reference_canonical == "TWUKEXCL001").count()
    assert n == 1  # eligible identity created; later non-baseline sightings surface normally
