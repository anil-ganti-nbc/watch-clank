"""Novelty semantic specimen corpus (2026-08-21 Phase 2/3).

Fifteen editorial specimens covering the full FIRST_SEEN_BY_CLANK vs
NEW_REFERENCE vs transition-event decision surface, plus the baseline-vs-
novelty first-run case. Each specimen is labelled with its intended
outcome; labels encode editorial intent, not current behaviour -- where
the code was wrong, the test forced a fix.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.pipeline import PipelineService
from app.services.snapshot_storage import SnapshotStorageService
from tests.test_core import FIXTURES, db_session, tmp_settings  # noqa: F401 -- shared fixtures


def _obs(watch_id, *, collector="timex_products", region="US", url=None, price=100.0,
         currency="USD", availability="AVAILABLE"):
    from app.models import SourceObservation

    return SourceObservation(
        watch_id=watch_id, collector_id=collector, collector_version="test", parser_id="test",
        parser_version="test", region=region, source_url=url or f"https://example.test/{watch_id}",
        price=price, currency=currency, availability_status=availability, overall_confidence=90.0,
    )


def _watch(db, *, ref, mfr="Timex", collection=None, extra=None):
    from app.models import Watch

    w = Watch(manufacturer=mfr, brand=mfr, reference_raw=ref, reference_canonical=ref,
              collection=collection, extra_specs=extra or {})
    db.add(w)
    db.flush()
    return w


def _transition(pipeline, watch, obs):
    return pipeline._record_product_transition(watch=watch, new_obs=obs, is_new_watch=True, experimental=True)


def _fresh(hours=2):
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


# --- Specimen 1: truly new official launch -----------------------------------


def test_specimen_1_official_launch_announcement_is_new_reference(db_session: Session, tmp_settings: Settings):
    """A first-party launch announcement naming an unseen reference is the
    strongest novelty evidence that exists -> NEW_REFERENCE via the news
    path."""
    from unittest.mock import patch

    from app.collectors.casio_intl_news import CasioIntlNewsCollector
    from app.collectors.casio_japan import CasioJapanCollector
    from app.models import CollectorRun, Event
    from app.services.snapshot_storage import SnapshotStorageService

    db_session.add(CollectorRun(collector_id="casio_multi", collector_version="0.1", status="SUCCESS"))
    db_session.flush()

    list_html = (FIXTURES / "casio_intl_news_list.html").read_bytes()
    detail = (FIXTURES / "casio_intl_news_efk200.html").read_bytes()

    def fake_collector_run(self, *args, **kwargs):
        from app.collectors.base import CollectorRunResult, DiscoveredItem, FetchResult
        from app.collectors.casio_intl_news import COLLECTOR_ID, COLLECTOR_VERSION

        result = CollectorRunResult(
            collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION,
            region="INTL", trust_score=90.0,
        )
        item = DiscoveredItem(url="https://www.casio-intl.com/news/efk200/", title="EDIFICE EFK200 launch")
        news_fr = FetchResult(
            url="https://www.casio-intl.com/news/efk200/", success=True, status_code=200,
            content_type="text/html", payload=detail,
        )
        result.discovered = [item]
        result.fetched = [news_fr]
        result.metadata["component_status"] = "SUCCESS"
        return result

    def fake_blocked(self, *args, **kwargs):
        from app.collectors.base import CollectorRunResult, FetchResult
        from app.collectors.casio_japan import COLLECTOR_ID as CAT_ID
        from app.collectors.casio_japan import COLLECTOR_VERSION as CAT_VER
        from app.collectors.casio_japan import TRUST_SCORE as CAT_TRUST

        result = CollectorRunResult(
            collector_id=CAT_ID, collector_version=CAT_VER, region="JP", trust_score=CAT_TRUST,
        )
        result.fetched = [FetchResult(url="https://blocked.example", success=False, error="403")]
        result.metadata["component_status"] = "BLOCKED"
        return result

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    with (
        patch.object(CasioIntlNewsCollector, "run", fake_collector_run),
        patch.object(CasioJapanCollector, "run", fake_blocked),
    ):
        run = pipeline.run_multi_source_pipeline(max_items=2, skip_lock=True, include_catalog=True)

    events = db_session.query(Event).all()
    assert len(events) >= 1
    assert all(e.event_type == "NEW_REFERENCE" for e in events)


# --- Specimens 2, 9: fossil first observed / reactivated relaunch -------------


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        ({}, "no timestamps at all"),
        ({"tags": ["Reactivated", "Backorder Eligible"], "published_at": _fresh(1)}, "reactivation tag"),
    ],
    ids=["fossil-no-evidence", "reactivated-relaunch"],
)
def test_specimen_2_and_9_weak_or_contra_evidence_never_claims_launch(
    db_session: Session, tmp_settings: Settings, extra, why
):
    from app.models import Event
    from app.services.snapshot_storage import SnapshotStorageService

    watch = _watch(db_session, ref="T2N092", extra=extra)  # a decade-old Easy Reader shape
    obs = _obs(watch.id)
    db_session.add(obs)
    db_session.flush()

    result = _transition(PipelineService(db_session, SnapshotStorageService(tmp_settings)), watch, obs)
    assert result["event_type"] == "FIRST_SEEN_BY_CLANK", why
    event = db_session.query(Event).one()
    ne = event.extra["novelty_evidence"]
    assert ne["evidence_strength"] == "WEAK"
    assert event.story_score < 25  # must not look like confident news


# --- Specimen 3: bulk-touched fresh timestamp --------------------------------


def test_specimen_3_bulk_touched_timestamp_cluster_is_not_a_launch(
    db_session: Session, tmp_settings: Settings
):
    """The exact Timex maintenance shape documented live on Hetzner: many
    UNRELATED collections sharing one fresh timestamp. A fresh published_at
    inside such a sync batch must NOT earn NEW_REFERENCE even though it is
    individually 'fresh' -- and the evidence must say why."""
    from app.models import Event
    from app.services.snapshot_storage import SnapshotStorageService

    stamp = _fresh(1)
    for i, coll in enumerate(["Waterbury Classic", "Easy Reader", "Weekender",
                              "Q Timex Marbella", "Expedition", "Ironman",
                              "Peanuts", "Legacy", "Marlin"]):  # 9 unrelated collections
        _watch(db_session, ref=f"TWBULK{i:03d}", collection=coll, extra={"published_at": stamp})
    target = _watch(db_session, ref="TWFOSSIL01", collection="Easy Reader", extra={"published_at": stamp})
    obs = _obs(target.id)
    db_session.add(obs)
    db_session.flush()

    result = _transition(PipelineService(db_session, SnapshotStorageService(tmp_settings)), target, obs)
    assert result["event_type"] == "FIRST_SEEN_BY_CLANK"
    event = db_session.query(Event).one()
    ne = event.extra["novelty_evidence"]
    assert ne["evidence_strength"] == "WEAK"
    assert "sync batch" in ne["classification_reason"]
    assert ne["cluster_shape"]["collections"] >= 3


def test_specimen_4_genuine_fresh_launch_without_announcement_earns_new_reference(
    db_session: Session, tmp_settings: Settings
):
    """A small single-collection sibling cluster with a fresh timestamp is
    the documented signature of a real coordinated family launch
    (Cavatina Luxe: 5 SKUs / 6 seconds) -> NEW_REFERENCE without any
    announcement article. Documented why: the source surface is Shopify's
    own product JSON, whose small tight clusters were verified live to be
    genuine launches, while cross-collection batches were not."""
    from app.models import Event
    from app.services.snapshot_storage import SnapshotStorageService

    stamp = _fresh(1)
    for i in range(2):  # siblings in the SAME collection
        _watch(db_session, ref=f"TWCAV{i:03d}", collection="Cavatina Luxe", extra={"published_at": stamp})
    target = _watch(db_session, ref="TWCAV003", collection="Cavatina Luxe", extra={"published_at": stamp})
    obs = _obs(target.id)
    db_session.add(obs)
    db_session.flush()

    result = _transition(PipelineService(db_session, SnapshotStorageService(tmp_settings)), target, obs)
    assert result["event_type"] == "NEW_REFERENCE"
    event = db_session.query(Event).one()
    ne = event.extra["novelty_evidence"]
    assert ne["evidence_strength"] == "MEDIUM"
    assert "affirmative" in ne["classification_reason"]


# --- Specimen 5: the 73-hour launch ------------------------------------------


def test_specimen_5_seventy_three_hour_launch_is_honest_first_seen_but_recoverable(
    db_session: Session, tmp_settings: Settings
):
    """A genuine launch first seen 73h after publication misses the tight
    freshness window -> honestly FIRST_SEEN_BY_CLANK locally, but MUST be
    recoverable through the human-reviewed baseline catch-up path rather
    than being permanently absorbed."""
    from datetime import datetime

    from app.models import CollectorRun, Event
    from app.services.snapshot_storage import SnapshotStorageService

    published = datetime.now(UTC) - timedelta(hours=73)
    watch = _watch(db_session, ref="TW2Y86600VQ", extra={"published_at": published.isoformat()})
    obs = _obs(watch.id)
    obs.is_baseline = True  # discovered during a baseline sweep
    db_session.add(obs)
    db_session.add(CollectorRun(collector_id="timex_products", collector_version="t", status="SUCCESS",
                                is_baseline=True))
    db_session.flush()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    result = _transition(pipeline, watch, obs)
    assert result["event_type"] == "FIRST_SEEN_BY_CLANK"

    candidates = pipeline.find_baseline_catchup_candidates(manufacturer="Timex")
    matches = [c for c in candidates if c["reference_canonical"] == "TW2Y86600VQ"]
    assert matches, "a 73h-old launch must remain visible to the human catch-up review"
    assert matches[0]["age_days"] == pytest.approx(73 / 24, abs=0.1)

    outcomes = pipeline.create_baseline_catchup_events(watch_ids=[matches[0]["watch_id"]])
    assert outcomes[0]["created"] is True
    belated = db_session.query(Event).filter(Event.event_type == "NEW_REFERENCE").all()
    assert len(belated) == 1
    assert belated[0].extra.get("belated_baseline_catchup") is True


# --- Specimen 6: US launch of an 18-month-old European model ------------------


def test_specimen_6_region_first_availability_is_new_region_not_new_reference(
    db_session: Session, tmp_settings: Settings
):
    from app.models import SourceObservation
    from app.services.snapshot_storage import SnapshotStorageService

    watch = _watch(db_session, ref="GBA-950-1A", mfr="Casio")
    eu = SourceObservation(
        watch_id=watch.id, collector_id="casio_europe_sitemap", collector_version="t", parser_id="t",
        parser_version="t", region="EU", source_url="https://casio.com/eu/gba-950", price=None,
        currency=None, availability_status=None, overall_confidence=70.0,
    )
    us = _obs(watch.id, collector="timex_products".replace("timex_products", "citizen_products"),
              region="US", url="https://example.test/us")
    db_session.add_all([eu, us])
    db_session.flush()

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    result = pipeline._record_product_transition(watch=watch, new_obs=us, is_new_watch=False, experimental=True)
    assert result["event_type"] == "NEW_REGION"


# --- Specimens 7, 8: restock semantics ----------------------------------------


def test_specimen_7_restock_after_documented_oos(db_session: Session, tmp_settings: Settings):
    from app.services.snapshot_storage import SnapshotStorageService

    watch = _watch(db_session, ref="TWRESTOCK1")
    prior = _obs(watch.id, url="https://example.test/prior", availability="SOLD_OUT")
    db_session.add(prior)
    db_session.flush()
    current = _obs(watch.id, url="https://example.test/current", availability="AVAILABLE")
    db_session.add(current)
    db_session.flush()

    result = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
        watch=watch, new_obs=current, is_new_watch=False, experimental=True
    )
    assert result["event_type"] == "RESTOCK"


def test_specimen_8_current_stock_alone_is_not_a_restock(db_session: Session, tmp_settings: Settings):
    from app.services.snapshot_storage import SnapshotStorageService

    watch = _watch(db_session, ref="TWSTOCKED1")
    prior = _obs(watch.id, url="https://example.test/prior", availability="AVAILABLE")
    db_session.add(prior)
    db_session.flush()
    current = _obs(watch.id, url="https://example.test/current", availability="AVAILABLE")
    db_session.add(current)
    db_session.flush()

    result = PipelineService(db_session, SnapshotStorageService(tmp_settings))._record_product_transition(
        watch=watch, new_obs=current, is_new_watch=False, experimental=True
    )
    assert result["event_type"] is None


# --- Specimen 10: old SKU, new URL --------------------------------------------


def test_specimen_10_old_sku_with_new_url_resolves_identity_no_event(
    db_session: Session, tmp_settings: Settings
):

    _watch(db_session, ref="TW2Y71200VQ")  # catalogue identity incl. variant suffix

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    # A news post leaks the SKU WITHOUT the suffix via an image filename.
    watch, is_new = pipeline._resolve_or_create_watch(
        reference_raw="TW2Y71200", manufacturer="Timex", brand="Timex", collection=None,
        model_name=None, extra={}, correlation_id="c", run_id=None,
    )
    assert is_new is False
    assert watch.reference_canonical == "TW2Y71200VQ"


# --- Specimen 11: suffix variant identity (known live ambiguity) --------------


def test_specimen_11_suffix_variants_are_distinct_watches_today(db_session: Session, tmp_settings: Settings):
    """TW4B20700 vs TW4B207009J coexist as separate Watches today: the
    prefix auto-link only fires when it resolves to EXACTLY one existing
    watch, so both orderings stay distinct once both exist. This pins the
    CURRENT conservative behaviour explicitly: no destructive normalization,
    no silent merge, and the ambiguity is documented as a known duplicate
    class rather than hidden."""
    from app.models import Watch

    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    base, _ = pipeline._resolve_or_create_watch(
        reference_raw="TW4B20700", manufacturer="Timex", brand="Timex", collection=None,
        model_name=None, extra={}, correlation_id="c1", run_id=None,
    )
    jdm, _ = pipeline._resolve_or_create_watch(
        reference_raw="TW4B207009J", manufacturer="Timex", brand="Timex", collection=None,
        model_name=None, extra={}, correlation_id="c2", run_id=None,
    )
    assert base.id != jdm.id
    assert db_session.query(Watch).count() == 2


# --- Specimen 12: specialist report only --------------------------------------


def test_specimen_12_specialist_report_creates_lead_not_launch_claim(
    db_session: Session, tmp_settings: Settings
):
    from app.models import Event
    from app.services.specialist_leads import SpecialistLeadService, classify_lead_type

    lead_type = classify_lead_type(
        title="Hands-on: the new Seiko SPB423 field watch",
        reference_candidates=["SPB423"],
    )
    assert lead_type == "POSSIBLE_NEW_REFERENCE"

    _watch(db_session, ref="SPB423", mfr="Seiko")  # official catalogue already knows it
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(
        source_id="monochrome", lead_type=lead_type, title="Hands-on: the new Seiko SPB423",
        source_url="https://monochrome.example/spb423", published_at=_fresh(5),
        reference_candidates=["SPB423"], claim_text=None, manufacturer="Seiko",
    )
    correlated = svc.correlate_pending_leads(manufacturer="Seiko")

    assert outcome["created"] is True
    assert len(correlated) == 1 and correlated[0]["correlation_type"] == "EXACT_REFERENCE_MATCH"
    # Two weak-ish sources agreeing must still NOT fabricate a NEW_REFERENCE Event.
    assert db_session.query(Event).count() == 0


# --- Specimen 13: future retailer prelisting ----------------------------------


def test_specimen_13_retailer_prelisting_is_an_early_retail_lead(
    db_session: Session, tmp_settings: Settings
):
    """A retailer listing ahead of official launch is journalistically hot;
    it must surface as a reviewable FRESH SpecialistLead -- never silently
    suppressed, never promoted to our own NEW_REFERENCE Event. Sale
    language WITHOUT a reference classifies EARLY_RETAIL_LISTING; with a
    reference it is POSSIBLE_NEW_REFERENCE (the reference is real evidence;
    the label stays lead-level either way)."""
    from app.models import Event
    from app.services.specialist_leads import SpecialistLeadService, classify_lead_type

    lead_type = classify_lead_type(title="Casio G-Shock GMW-B5000CS-1 now available for preorder",
                                   reference_candidates=["GMW-B5000CS"])
    svc = SpecialistLeadService(db_session)
    outcome = svc.ingest_candidate(
        source_id="deployant", lead_type=lead_type,
        title="Casio G-Shock GMW-B5000CS-1 now available for preorder",
        source_url="https://retailer.example/gmwb5000cs", published_at=_fresh(3),
        reference_candidates=["GMW-B5000CS"], claim_text=None, manufacturer="Casio",
        ingestion_method="manual",
    )
    from app.models import SpecialistLead

    assert outcome["created"] is True
    lead = db_session.get(SpecialistLead, outcome["lead_id"])
    assert lead.editorial_freshness == "FRESH"
    assert db_session.query(Event).count() == 0

    # sale language without a reference -> EARLY_RETAIL_LISTING
    assert classify_lead_type(
        title="This week's watch deals include several G-Shocks on sale",
        reference_candidates=[],
    ) == "EARLY_RETAIL_LISTING"


# --- Specimen 14: leaked image, unknown reference -----------------------------


def test_specimen_14_leak_without_reference_is_leaked_image_not_fabrication():
    from app.services.specialist_leads import classify_lead_type

    assert classify_lead_type(
        title="Spy shots: unannounced G-Shock spotted before its official announcement",
        claim_text="accidentally published product page",
        reference_candidates=[],
    ) == "LEAKED_IMAGE"


# --- Specimen 15: accessory with watch-like SKU -------------------------------


def test_specimen_15_accessory_sale_is_not_a_watch_reference():
    from app.services.specialist_leads import classify_lead_type

    # The Atelier NBR strap class: sale language + a real-looking SKU.
    assert classify_lead_type(
        title="Timex Atelier NBR strap now available separately",
        reference_candidates=["TW7D18600"],
    ) == "EARLY_RETAIL_LISTING"


# --- Phase 3: first run mixing historical inventory with genuine launches -----


def _listing_page(products):
    """products: list of (sku, title, ygroup, published_iso|None)."""
    payload = {
        "products": [
            {
                "id": i + 1, "title": title, "handle": sku.lower(), "product_type": "Watch",
                "published_at": pub, "tags": ([f"YGroup_{ygroup}"] if ygroup else []),
                "variants": [{"sku": sku, "price": "99.00", "available": True}],
            }
            for i, (sku, title, ygroup, pub) in enumerate(products)
        ]
    }
    return json.dumps(payload).encode("utf-8")


def test_phase3_first_run_baselines_history_but_survives_genuine_launches(
    db_session: Session, tmp_settings: Settings
):
    """THE hard case: a newly introduced collector's first fetch contains a
    thousand historical products and three launches published today.
    Correct outcome: historical items baselined silently; the three
    affirmative-launch items emit NEW_REFERENCE; nothing is suppressed
    forever; nothing floods."""
    from app.models import Event
    from app.services.snapshot_storage import SnapshotStorageService

    old = [(f"TWOLD{i:04d}", f"Historical Model {i}", None, None) for i in range(50)]
    fresh_stamp = _fresh(1)
    launches = [(f"TWNEW{i:03d}", f"Genuine Launch {i}", None, fresh_stamp) for i in range(3)]

    empty = (FIXTURES / "timex_products_page_empty.json").read_bytes()
    pipeline = PipelineService(db_session, SnapshotStorageService(tmp_settings))
    run = pipeline.run_product_observation_pipeline("timex", offline_fixture=[
        _listing_page([*old, *launches]), empty,
    ])

    assert run.summary_metadata["auto_baseline_applied"] is True
    assert run.new_watch_count == 53
    events = db_session.query(Event).all()
    new_refs = [e for e in events if e.event_type == "NEW_REFERENCE"]
    firsts = [e for e in events if e.event_type == "FIRST_SEEN_BY_CLANK"]
    assert {e.title.split(":")[0] for e in new_refs} == {"Timex TWNEW000", "Timex TWNEW001", "Timex TWNEW002"}
    assert len(firsts) == 0  # historical inventory stayed silent, not reclassified as noise-events


# --- Alert rendering: uncertainty must be visible -----------------------------


def test_first_seen_alert_cannot_resemble_confirmed_launch():
    from app.services.editorial import EventEvidence, format_alert, score_event

    first_seen = format_alert(
        manufacturer="Timex", brand="Timex", reference_raw="TWOLD001",
        scored=score_event(EventEvidence(event_type="FIRST_SEEN_BY_CLANK", manufacturer="Timex", brand="Timex")),
        region="US", announcement_title="Old model", announcement_url="https://x",
        observed_at="2026-08-21T00:00:00Z",
    )
    new_ref = format_alert(
        manufacturer="Timex", brand="Timex", reference_raw="TWNEW001",
        scored=score_event(EventEvidence(event_type="NEW_REFERENCE", manufacturer="Timex", brand="Timex")),
        region="US", announcement_title="New model", announcement_url="https://x",
        observed_at="2026-08-21T00:00:00Z",
    )
    assert "UNCONFIRMED" in first_seen and "needs review" in first_seen
    assert "UNCONFIRMED" not in new_ref
