"""September 30 regressions: exact missed references, durable refresh progress."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.collectors.casio_europe_sitemap import CasioEuropeSitemapCollector
from app.collectors.casio_jp_sitemap import CasioJPSitemapCollector
from app.collectors.casio_uk_sitemap import CasioUKSitemapCollector
from app.collectors.citizen_products import CitizenProductsCollector
from app.models import CollectorRun, Event, SourceObservation, SpecialistLead
from app.parsers.casioblog import parse_casioblog_feed
from app.parsers.citizen_products import parse_citizen_search_hit
from app.parsers.gcentral import parse_gcentral_feed
from app.parsers.specialist_publications import parse_specialist_publication_feed
from app.sentinel.adapters import poll_source
from app.sentinel.config import SENTINEL_SOURCES
from app.services.pipeline import PipelineService
from app.services.snapshot_storage import SnapshotStorageService
from tests.test_core import db_session, tmp_settings  # noqa: F401
from tests.test_sentinel import _fetch_map

FIXTURES = Path(__file__).parent / "fixtures"


def _sitemap(region, refs):
    return ("<urlset>" + "".join(
        f"<url><loc>https://www.casio.com/{region}/watches/casio/product.{ref}/</loc>"
        "<lastmod>2026-09-29</lastmod></url>" for ref in refs
    ) + "</urlset>").encode()


@pytest.mark.parametrize("collector,region", [
    (CasioUKSitemapCollector, "uk"), (CasioJPSitemapCollector, "jp"),
    (CasioEuropeSitemapCollector, "europe"),
])
def test_known_f_b100_refresh_is_not_permanently_starved(collector, region):
    c = collector()
    payload = _sitemap(region, [f"F-91W-{i}A" for i in range(305)] + ["F-B100W-1A"])
    all_items = c.discover_from_sitemap_xml(payload)
    now = datetime.now(UTC)
    ages = {i.url: now for i in all_items}
    target = all_items[-1].url
    ages[target] = now - timedelta(days=30)
    # An unseen reference remains ahead of even the oldest known reference.
    known = set(ages) - {all_items[0].url}
    first = c.run(max_items=2, sitemap_payload=payload, known_product_urls=known, last_observed_by_url=ages)
    assert [i.reference_hint for i in first.discovered] == ["F-91W-0A", "F-B100W-1A"]
    assert first.metadata["selection"]["policy"] == "unseen_first_oldest_observed"
    ages[target] = now + timedelta(seconds=1)
    second = c.run(max_items=2, sitemap_payload=payload, known_product_urls=set(ages), last_observed_by_url=ages)
    assert target not in [i.url for i in second.discovered]


def test_pipeline_refresh_progresses_using_committed_observations(db_session, tmp_settings):  # noqa: F811
    """Identical sitemap payloads still advance the next selected refresh slice."""
    pipe = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    payload = _sitemap("uk", ["F-91W-1A", "F-91W-3A", "F-B100W-1A"])
    first = pipe.run_product_observation_pipeline(brand="casio_uk", offline_fixture=payload, force_baseline=True)
    assert first.status == "SUCCESS"
    old = datetime.now(UTC) - timedelta(days=30)
    db_session.query(SourceObservation).update({SourceObservation.observed_at: old})
    db_session.commit()
    pipe.run_product_observation_pipeline(brand="casio_uk", offline_fixture=payload, max_items=1, emit_events=False)
    pipe.run_product_observation_pipeline(brand="casio_uk", offline_fixture=payload, max_items=1, emit_events=False)
    refreshed = db_session.query(SourceObservation.source_url).filter(SourceObservation.observed_at > old).all()
    assert len({row[0] for row in refreshed}) == 2
    assert db_session.query(Event).count() == 0


def test_casio_discovery_includes_references_past_old_3000_ceiling():
    c = CasioJPSitemapCollector()
    payload = _sitemap("jp", [f"F-91W-{i}A" for i in range(3100)] + ["F-B100W-1B"])
    all_items = c.discover_from_sitemap_xml(payload)
    result = c.run(max_items=1, sitemap_payload=payload, known_product_urls={i.url for i in all_items[:-1]})
    assert len(all_items) == 3101
    assert result.discovered[0].reference_hint == "F-B100W-1B"


@pytest.mark.parametrize("parse", [parse_casioblog_feed, parse_gcentral_feed, parse_specialist_publication_feed])
def test_specialist_f_b100_and_dw5600_references(parse):
    feed = b"""<rss><channel><item><title>Casio F-B100W-1A restock and BAIT x G-Shock DW-5600</title>
    <link>https://example.test/casio-update</link><pubDate>Wed, 30 Sep 2026 04:49:44 GMT</pubDate>
    </item></channel></rss>"""
    result = parse(feed)
    assert result.success
    # Single-brand feeds deliberately extract family roots; multi-brand
    # publications already retain full variants. Preserve both contracts.
    f_reference = "F-B100W-1A" if parse is parse_specialist_publication_feed else "F-B100W"
    assert result.items[0].reference_candidates == ["DW-5600", f_reference]


@pytest.mark.parametrize("parse", [parse_casioblog_feed, parse_gcentral_feed, parse_specialist_publication_feed])
def test_f_prefix_does_not_admit_prose_with_digits(parse):
    feed = b"""<rss><channel><item><title>Casio flash2026 fan3 update</title>
    <link>https://example.test/casio-update</link></item></channel></rss>"""
    assert parse(feed).items[0].reference_candidates == []


def test_citizen_received_date_is_preserved_without_inventing_publication():
    payload = (FIXTURES / "citizen_new_arrivals_at8296.html").read_bytes()
    item = CitizenProductsCollector().parse_search_page(payload.decode())[0][0]
    watch = parse_citizen_search_hit(item.metadata["product_dict"]).watches[0]
    assert watch.reference_raw == "AT8296-53A"
    assert watch.extra_specs["website_first_received_date"] == "2026-09-30"
    assert watch.extra_specs["source_badges"] == ["new", "exclusive"]
    assert "published_at" not in watch.extra_specs
    assert watch.availability_status is None


def test_timex_india_actual_misses_are_discoverable_with_region_and_full_sku():
    cfg = SENTINEL_SOURCES["timex_in"]
    payload = (FIXTURES / "timex_india_recent_misses.json").read_bytes()
    outcome = poll_source(cfg, _fetch_map({cfg.listing_url_template.format(page=1): payload}))
    assert cfg.enabled and cfg.cadence_minutes == 15
    assert outcome.status == "SUCCESS" and outcome.request_count == 1
    assert {c.reference_raw for c in outcome.candidates} == {"TWG027900JQ", "TW2Y70600UJ"}
    assert {c.region for c in outcome.candidates} == {"IN"}


def _citizen_page(refs, total):
    return ("<script>" + json.dumps({"data": {"limit": 48, "effectiveSearchMode": "search", "total": total,
        "hits": [{"representedProduct": {"id": ref}, "productName": ref} for ref in refs]}}) + "</script>").encode()


def test_citizen_fast_discovery_follows_pages_and_rejects_partial_baseline():
    cfg = SENTINEL_SOURCES["citizen_us_mens_new"]
    pages = {cfg.listing_url_template.format(offset=0): _citizen_page([f"AT{i:04d}-53A" for i in range(48)], 49),
        cfg.listing_url_template.format(offset=48): _citizen_page(["AT8296-53A"], 49)}
    outcome = poll_source(cfg, _fetch_map(pages))
    assert outcome.status == "SUCCESS" and len(outcome.candidates) == 49
    assert outcome.request_count == 2
    del pages[cfg.listing_url_template.format(offset=48)]
    failed = poll_source(cfg, _fetch_map(pages))
    assert failed.status == "FAILED" and failed.candidates == []


@pytest.mark.parametrize("payload", [b"<html>not a listing</html>", _citizen_page(["AT8296-53A"], 2), _citizen_page(["AT8296-53A"], 200)])
def test_citizen_malformed_incomplete_or_over_budget_discovery_fails_closed(payload):
    cfg = SENTINEL_SOURCES["citizen_us_mens_new"]
    outcome = poll_source(cfg, _fetch_map({cfg.listing_url_template.format(offset=0): payload}))
    assert outcome.status == "FAILED" and outcome.candidates == []


def test_gcentral_generated_timer_has_faster_cadence(tmp_path):
    from scripts.systemd.docker.render_units import render
    render(tmp_path)
    assert "OnUnitActiveSec=900" in (tmp_path / "watch-clank-gcentral-rss.timer").read_text()


def test_source_delay_cannot_be_hidden_by_fast_delivery(db_session):  # noqa: F811
    from app.services.specialist_leads import SpecialistLeadService, _attach_lead_delivery_report
    run = CollectorRun(collector_id="gcentral_rss", collector_version="test", status="SUCCESS")
    db_session.add(run)
    db_session.commit()
    pub = datetime(2026, 9, 30, 4, 49, 44, tzinfo=UTC)
    svc = SpecialistLeadService(db_session)
    result = svc.ingest_candidate(source_id="g_central", lead_type="POSSIBLE_COLLABORATION",
        title="BAIT x G-Shock DW5600", source_url="https://example.test/bait", published_at=pub.isoformat(),
        reference_candidates=["DW5600"], claim_text=None, collector_run_id=run.id)
    lead = db_session.get(SpecialistLead, result["lead_id"])
    lead.discovered_at = pub + timedelta(seconds=2244)
    lead.notified_at = lead.discovered_at + timedelta(seconds=1)
    svc.record_lead_delivery_outcome(lead, "provider_identified", "PROVIDER_MESSAGE_IDENTIFIED")
    db_session.commit()
    latency = _attach_lead_delivery_report(db_session, run, {})["new_lead_latency"]
    assert latency["publication_to_discovery_seconds"] == {"samples": 1, "max": 2244}
    assert latency["discovery_to_provider_acceptance_seconds"] == {"samples": 1, "max": 1}
    lead.published_at = lead.discovered_at + timedelta(hours=9)
    latency = _attach_lead_delivery_report(db_session, run, {})["new_lead_latency"]
    assert latency["publication_to_discovery_seconds"]["samples"] == 0
    assert latency["invalid_timestamp_order_count"] == 1


def test_official_collaboration_archive_uses_campaign_urls_and_ignores_navigation():
    cfg = SENTINEL_SOURCES["gshock_us_collaborations"]
    payload = (FIXTURES / "gshock_us_collaboration_cards.html").read_bytes()
    ignored = b'''<a href="https://other.test/us/products/collection/limited/bait/">Foreign</a>
    <a href="/us/products/collection/limited/">Navigation</a>
    <a href="/us/contents/nigo-gshock/">Figures</a>
    <a href="/us/products/collection/limited/toyo_tires/?tracking=1">Duplicate</a>'''
    payload = payload.replace(b"</main>", ignored + b"</main>")
    outcome = poll_source(cfg, _fetch_map({cfg.listing_url_template: payload}))
    assert outcome.status == "SUCCESS" and outcome.request_count == 1
    assert len(outcome.candidates) == 2
    assert {c.title for c in outcome.candidates} == {"Toyo Tires DW5600TT25-1", "JAE TIPS DW6900JT25-2"}
    assert all(c.reference_raw is None and c.region == "US" for c in outcome.candidates)
    failed = poll_source(cfg, _fetch_map({cfg.listing_url_template: b"<main>No collaboration links</main>"}))
    assert failed.status == "FAILED" and not failed.candidates
