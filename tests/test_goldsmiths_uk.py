"""Regression coverage for the isolated Goldsmiths UK soak lane."""

import json

from app.collectors.goldsmiths_uk_retailer import (
    DETAIL_FETCH_CAP,
    PRODUCT_SITEMAP_PREFIX,
    GoldsmithsUkRetailerCollector,
    extract_reference_hint,
)
from app.parsers.goldsmiths_uk_retailer import parse_goldsmiths_uk_product_html
from app.parsers.manual_uk_evidence import parse_manual_uk_evidence
from tests.test_core import db_session, tmp_settings  # noqa: F401 -- shared DB fixtures


def _child_xml(urls: list[str]) -> bytes:
    return (
        "<urlset>"
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>"
    ).encode()


def _detail(reference: str, *, price: int = 279, stock: str = "inStock") -> bytes:
    state = {
        "product": {
            "mpn": reference,
            "manufacturer": "Citizen",
            "name": f"Citizen {reference}",
            "price": {"value": price, "currencyIso": "GBP"},
            "stockLevelStatus": stock,
            "purchasable": stock == "inStock",
        }
    }
    return (
        '<html><script id="ng-state" type="application/json">'
        + json.dumps(state)
        + "</script></html>"
    ).encode()


def _fixture(urls: list[str], details: dict[str, bytes]):
    child = PRODUCT_SITEMAP_PREFIX + "0.xml"
    return {
        "index": {"sitemap": [{"loc": child}]},
        "children": {child: _child_xml(urls)},
        "details": details,
    }


def test_reference_hint_preserves_literal_plus():
    url = "https://www.goldsmiths.co.uk/Citizen-Red-Arrows-Mens-Watch-+-Limited-Edition-CA0080+54E/p/123"
    assert extract_reference_hint(url) == "CA0080+54E"


def test_full_child_sitemap_filter_prevents_budget_starvation():
    late_url = "https://www.goldsmiths.co.uk/Citizen-Late-Listing-AW1911-53A/p/1"
    noise = [f"https://www.goldsmiths.co.uk/Other-Product-{n}/p/{n}" for n in range(3_500)]
    urls = noise[:3_409] + [late_url] + noise[3_409:]
    result = GoldsmithsUkRetailerCollector().run(
        sitemap_payload=_fixture(urls, {late_url: _detail("AW1911-53A")}),
        max_items=1,
    )
    assert result.metadata["raw_sitemap_url_count"] == len(urls)
    assert result.metadata["filtered_candidate_count"] == 1
    assert [item.url for item in result.discovered] == [late_url]
    assert result.metadata["component_status"] == "SUCCESS"


def test_detail_budget_is_independent_from_full_sitemap_pass():
    urls = [
        f"https://www.goldsmiths.co.uk/Citizen-{n}-AW1911-{n:02d}/p/{n}"
        for n in range(61)
    ]
    details = {
        url: _detail(f"AW1911-{n:02d}")
        for n, url in enumerate(urls)
    }
    result = GoldsmithsUkRetailerCollector().run(
        sitemap_payload=_fixture(urls, details),
        max_items=300,
    )
    assert result.metadata["filtered_candidate_count"] == 61
    assert result.metadata["detail_fetch_cap"] == DETAIL_FETCH_CAP
    assert len(result.fetched) == DETAIL_FETCH_CAP


def test_goldsmiths_parser_uses_detail_mpn_and_maps_gbp_stock():
    parsed = parse_goldsmiths_uk_product_html(_detail("AW1914-55L", price=299))
    assert parsed.success
    watch = parsed.watches[0]
    assert watch.reference_raw == "AW1914-55L"
    assert watch.price == 299.0 and watch.currency == "GBP"
    assert watch.availability_status == "AVAILABLE"
    assert "third_party_retailer_evidence" in watch.parser_warnings


def test_goldsmiths_parser_rejects_non_gbp_price():
    html = (
        '<script id="ng-state" type="application/json">'
        + json.dumps({"product": {"mpn": "AW1911-53E", "manufacturer": "Citizen", "price": {"value": 279, "currencyIso": "EUR"}}})
        + "</script>"
    )
    parsed = parse_goldsmiths_uk_product_html(html)
    assert not parsed.success and "unexpected currency" in parsed.error


def test_goldsmiths_parser_rejects_price_without_currency():
    html = (
        '<script id="ng-state" type="application/json">'
        + json.dumps({"product": {"mpn": "AW1911-53E", "manufacturer": "Citizen", "price": {"value": 279}}})
        + "</script>"
    )
    parsed = parse_goldsmiths_uk_product_html(html)
    assert not parsed.success and "currency missing" in parsed.error


def test_goldsmiths_parser_accepts_literal_zero_price():
    """A real 0 value must not silently fall through to formattedValue."""
    html = (
        '<script id="ng-state" type="application/json">'
        + json.dumps({"product": {"mpn": "AW1911-53E", "manufacturer": "Citizen", "price": {"value": 0, "currencyIso": "GBP"}}})
        + "</script>"
    )
    parsed = parse_goldsmiths_uk_product_html(html)
    assert parsed.success
    assert parsed.watches[0].price == 0.0 and parsed.watches[0].currency == "GBP"


def test_offline_fixture_without_details_never_touches_the_network(monkeypatch):
    """A fixture index without a details mapping is a broken fixture, not
    permission to fetch live pages from supposedly-offline test code."""
    from app.collectors import goldsmiths_uk_retailer as collector_module

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError("live fetch attempted in offline fixture mode")

    monkeypatch.setattr(collector_module, "fetch_url", forbidden_fetch)
    child = PRODUCT_SITEMAP_PREFIX + "0.xml"
    url = "https://www.goldsmiths.co.uk/Citizen-AW1911-53A/p/1"
    result = collector_module.GoldsmithsUkRetailerCollector().run(
        sitemap_payload={
            "index": {"sitemap": [{"loc": child}]},
            "children": {child: _child_xml([url])},
        },
        max_items=5,
    )
    assert len(result.fetched) == 1
    assert not result.fetched[0].success
    assert result.fetched[0].error == "missing offline detail fixture"
    assert result.metadata["component_status"] == "FAILED"
    assert result.metadata["healthy"] is False


def test_manual_uk_evidence_requires_attestation_and_gbp():
    good = parse_manual_uk_evidence({
        "reference": "AW1911-53A",
        "source_url": "https://www.citizenwatch.co.uk/product/AW1911-53A",
        "submitter": "operator",
        "captured_at": "2026-08-29T10:00:00+05:30",
        "price": 279,
        "currency": "GBP",
        "availability": "AVAILABLE",
        "operator_confirmed": True,
    })
    assert good.success
    assert good.watches[0].extra_specs["manual_evidence"]["operator_confirmed"] is True

    unconfirmed = parse_manual_uk_evidence({
        "reference": "AW1911-53A",
        "source_url": "https://example.test/AW1911-53A",
        "submitter": "operator",
        "captured_at": "2026-08-29T10:00:00+05:30",
        "operator_confirmed": False,
    })
    assert not unconfirmed.success and "operator_confirmed" in unconfirmed.error


def test_goldsmiths_pipeline_preserves_retailer_provenance_and_delivery_silence(db_session, tmp_settings):  # noqa: F811
    from app.collectors.base import FetchResult
    from app.models import Event, SourceObservation, Watch
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    watch = Watch(
        manufacturer="Citizen",
        brand="Citizen",
        reference_raw="AW1911-53A",
        reference_canonical="AW1911-53A",
    )
    db_session.add(watch)
    db_session.flush()
    db_session.add(
        SourceObservation(
            watch_id=watch.id,
            collector_id="citizen_products",
            collector_version="test",
            parser_id="test",
            parser_version="test",
            region="US",
            source_url="https://www.citizenwatch.com/us/en/product/AW1911-53A",
            price=279,
            currency="USD",
            availability_status="AVAILABLE",
            overall_confidence=90,
        )
    )
    db_session.flush()

    detail_url = "https://www.goldsmiths.co.uk/Citizen-AW1911-53A/p/1"
    outcome = pipeline.process_fetch_result(
        FetchResult(
            url=detail_url,
            success=True,
            status_code=200,
            content_type="text/html",
            payload=_detail("AW1911-53A"),
            metadata={"reference_hint": "AW1911-53A", "discovery_role": "product_detail"},
        ),
        run_id=None,
        collector_id="goldsmiths_uk_retailer",
        collector_version="0.1.0",
        parse_fn=parse_goldsmiths_uk_product_html,
        default_region="GB",
        emit_events=True,
        notify=False,
        experimental=True,
        source_trust_score=70.0,
        is_first_party=False,
        evidence_grade="THIRD_PARTY_RETAILER_LISTING",
        source_class="RETAILER",
    )

    assert outcome["success"]
    observation = db_session.query(SourceObservation).filter_by(collector_id="goldsmiths_uk_retailer").one()
    assert observation.region == "GB" and observation.source_trust_score == 70.0
    assert observation.fetch.extra_metadata["discovery_role"] == "product_detail"
    event = db_session.query(Event).filter_by(event_type="NEW_REGION").one()
    assert event.extra["source_provenance"] == {
        "is_first_party": False,
        "evidence_grade": "THIRD_PARTY_RETAILER_LISTING",
        "source_class": "RETAILER",
    }
    assert not event.extra["alerted"]


def test_goldsmiths_product_registry_runs_silent_baseline(db_session, tmp_settings):  # noqa: F811
    from app.models import Event, SourceObservation
    from app.services.pipeline import PipelineService
    from app.services.snapshot_storage import SnapshotStorageService

    detail_url = "https://www.goldsmiths.co.uk/Citizen-AW1911-53E/p/1"
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = pipeline.run_product_observation_pipeline(
        "goldsmiths_uk",
        offline_fixture=_fixture([detail_url], {detail_url: _detail("AW1911-53E")}),
        max_items=1,
        force_baseline=True,
    )
    assert run.status == "SUCCESS"
    assert run.summary_metadata["source_class"] == "RETAILER"
    assert run.summary_metadata["detail_fetch_count"] == 1
    assert db_session.query(SourceObservation).filter_by(collector_id="goldsmiths_uk_retailer").count() == 1
    assert db_session.query(Event).count() == 0
