"""Horology Sentinel regression tests.

Covers the operator's 13 required behaviours with fully offline fixtures:
no test touches the network. Feed payloads mirror live shapes verified
2026-09-08 from the Hetzner vantage (Shopify products.json with variant
SKUs; sitemap XML with product.<ref>/ URLs).

Deployment shape reflected throughout: a source's FIRST successful poll is
its silent per-source baseline (the auto-baseline law -- a new source can
never replay its catalogue into Discord); every poll after that alerts
genuinely unseen identities. The explicit --sentinel-baseline force has
the same silence by definition.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.collectors.base import FetchResult
from app.models import DeliveryReceipt
from app.models.base import Base
from app.models.sentinel import (
    ADMITTED_VIA_BASELINE,
    SentinelIdentity,
    SentinelSighting,
    SentinelSourceState,
)
from app.sentinel.alerting import (
    ENTITY_SENTINEL_SIGHTING,
    PURPOSE_SENTINEL_SIGHTING,
    SentinelAlerter,
    format_sighting,
)
from app.sentinel.identity import build_identity, collapse_regional_suffix
from app.sentinel.runner import SentinelRunner
from app.services.discord_notify import DeliveryAttempt
from tests.test_core import db_session, tmp_settings  # noqa: F401 -- shared fixtures

# ---------------------------------------------------------------------------
# offline fixtures


def _shopify_payload(skus_and_titles: list[tuple[str | None, str]], price: str = "279.00") -> bytes:
    products = []
    for i, (sku, title) in enumerate(skus_and_titles):
        handle = title.lower().replace(" ", "-")[:40] or f"handle-{i}"
        products.append(
            {
                "id": 1000 + i,
                "title": title,
                "handle": handle,
                "product_type": "Watch",
                "published_at": "2026-09-08T14:19:37+01:00",
                "variants": [{"sku": sku, "price": price}],
            }
        )
    return json.dumps({"products": products}).encode()


_TIMEX_US = "https://www.timex.com/products.json?limit=250&page=1"
_TIMEX_UK = "https://timex.co.uk/products.json?limit=250&page=1"

_CASIO_UK_SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.casio.com/uk/watches/gshock/product.GA-2100-1A1/</loc><lastmod>2026-09-01T10:00:00Z</lastmod></url>
<url><loc>https://www.casio.com/uk/watches/gshock/product.GWF-A1000AP-1A/</loc><lastmod>2026-09-02T10:00:00Z</lastmod></url>
</urlset>"""


def _fetch_map(routes: dict[str, bytes | Callable[[], Exception]]):
    """Build a fetch_fn dispatching on URL prefix; unknown URLs 404."""

    def fetch(url: str) -> FetchResult:
        for prefix, payload in routes.items():
            if url.startswith(prefix):
                if callable(payload):
                    raise payload()
                return FetchResult(url=url, success=True, status_code=200, payload=payload)
        return FetchResult(url=url, success=False, status_code=404, error="HTTP 404")

    return fetch


class RecordingNotifier:
    """Duck-typed DiscordNotifier: records calls, never touches network."""

    def __init__(self, accepted: bool = True, *, editorial_enabled: bool = True):
        self.accepted = accepted
        self.editorial_enabled = editorial_enabled
        self.calls: list[str] = []

    def send_editorial_alert_detailed(self, text: str) -> DeliveryAttempt:
        self.calls.append(text)
        if not self.accepted:
            return DeliveryAttempt(
                accepted=False, provider_status=500, destination_alias="editorial:testfingerprint",
                error_summary="HTTP 500",
            )
        return DeliveryAttempt(
            accepted=True,
            provider_status=200,
            provider_message_id="m-1",
            provider_channel_id="c-1",
            destination_alias="editorial:testfingerprint",
        )


@pytest.fixture()
def make_runner(db_session, tmp_settings):  # noqa: F811 -- pytest fixture
    """Factory: build a SentinelRunner wired to the test session/settings."""

    def make(*, fetch=None, notifier=None):
        alerter = SentinelAlerter(db_session, tmp_settings, notifier=notifier or RecordingNotifier())
        return SentinelRunner(db_session, tmp_settings, fetch_fn=fetch, alerter=alerter)

    return make


def run_sweep(make_runner, notifier, **kwargs):
    fetch = kwargs.pop("fetch", None)
    return make_runner(fetch=fetch, notifier=notifier).run_sweep(**kwargs)


def _arm(make_runner, notifier, source: str, url: str, payload: bytes):
    """Silently arm a source: its first successful poll is its baseline."""
    return run_sweep(make_runner, notifier, only_source=source, fetch=_fetch_map({url: payload}))


# ---------------------------------------------------------------------------
# 1. unseen model number produces one sighting


def test_unseen_model_produces_one_sighting(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    run = run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map(
            {
                _TIMEX_US: _shopify_payload(
                    [("TW2Y99000", "Existing Timex"), ("TW5M74100", "TIMEX IRONMAN Adrenaline 46.5mm")]
                )
            }
        ),
    )
    assert run.status == "SUCCESS"
    totals = run.summary_metadata["totals"]
    assert totals["unseen_admitted"] == 1
    assert totals["alerts_sent"] == 1

    sightings = db_session.execute(select(SentinelSighting)).scalars().all()
    assert len(sightings) == 1
    assert sightings[0].reference == "TW5M74100"
    assert sightings[0].alert_state == "SENT"

    identity = (
        db_session.query(SentinelIdentity).filter_by(identity_key="timex:tw5m74100").one()
    )
    assert identity.admitted_via == "SIGHTING"


# ---------------------------------------------------------------------------
# 2. same model on next poll produces no second sighting


def test_same_model_next_poll_is_suppressed(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    feed = _fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN Adrenaline")])})
    run_sweep(make_runner, notifier, only_source="timex_us", fetch=feed)
    run3 = run_sweep(make_runner, notifier, only_source="timex_us", fetch=feed)

    assert run3.summary_metadata["totals"]["unseen_admitted"] == 0
    assert run3.summary_metadata["totals"]["known_suppressed"] == 1
    assert run3.summary_metadata["totals"]["alerts_sent"] == 0
    assert len(db_session.execute(select(SentinelSighting)).scalars().all()) == 1


# ---------------------------------------------------------------------------
# 3. same model in another region is deduplicated (TW5M74100UK -> TW5M74100)


def test_same_model_other_region_is_deduplicated(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    # Both sources armed with their own seed SKUs (silent baselines done).
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    _arm(make_runner, notifier, "timex_uk", _TIMEX_UK, _shopify_payload([("TW2Y05900UK", "Existing Timex UK")]))
    # US sights the model first: one alert.
    run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN Adrenaline")])}),
    )
    # UK lists the SAME model with its regional SKU suffix: deduped, no
    # second sighting.
    run2 = run_sweep(
        make_runner,
        notifier,
        only_source="timex_uk",
        fetch=_fetch_map({_TIMEX_UK: _shopify_payload([("TW5M74100UK", "TIMEX IRONMAN Adrenaline UK")])}),
    )

    assert run2.summary_metadata["totals"]["unseen_admitted"] == 0
    assert run2.summary_metadata["per_source"]["timex_uk"]["known_suppressed"] == 1
    assert len(notifier.calls) == 1  # only the original US sighting alerted
    identity = db_session.query(SentinelIdentity).filter_by(identity_key="timex:tw5m74100").one()
    assert identity.first_source == "timex_us"
    assert identity.reference_raw == "TW5M74100"  # first spelling is preserved
    sightings = db_session.execute(select(SentinelSighting)).scalars().all()
    assert len(sightings) == 1


# ---------------------------------------------------------------------------
# 4. two genuinely different references both alert


def test_two_different_references_both_alert(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    run = run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map(
            {
                _TIMEX_US: _shopify_payload(
                    [("TW5M74100", "TIMEX IRONMAN Adrenaline"), ("TW5M74200", "TIMEX Weekender")]
                )
            }
        ),
    )
    totals = run.summary_metadata["totals"]
    assert totals["unseen_admitted"] == 2
    assert totals["alerts_sent"] == 2
    keys = {r[0] for r in db_session.execute(select(SentinelIdentity.identity_key)).all()}
    assert keys == {"timex:tw2y99000", "timex:tw5m74100", "timex:tw5m74200"}


# ---------------------------------------------------------------------------
# 5. price/stock/content change on an existing product does not alert


def test_price_or_title_change_does_not_alert(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW5M74100", "TIMEX IRONMAN Adrenaline")]))
    run2 = run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map(
            {
                _TIMEX_US: _shopify_payload(
                    [("TW5M74100", "TIMEX IRONMAN Adrenaline (REFRESHED PAGE)")], price="199.00"
                )
            }
        ),
    )
    assert run2.summary_metadata["totals"]["unseen_admitted"] == 0
    assert run2.summary_metadata["totals"]["known_suppressed"] == 1
    assert notifier.calls == []


# ---------------------------------------------------------------------------
# 6. baseline imports identities without alerting


def test_baseline_imports_silently(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    run = run_sweep(
        make_runner,
        notifier,
        force_baseline=True,
        only_source="timex_us",
        fetch=_fetch_map(
            {
                _TIMEX_US: _shopify_payload(
                    [("TW5M74100", "TIMEX IRONMAN"), ("TW5M74200", "TIMEX Weekender")]
                )
            }
        ),
    )
    assert run.is_baseline is True
    assert run.summary_metadata["totals"]["unseen_admitted"] == 0
    assert run.summary_metadata["totals"]["alerts_sent"] == 0
    assert notifier.calls == []
    identities = db_session.execute(select(SentinelIdentity)).scalars().all()
    assert len(identities) == 2
    assert all(i.admitted_via == ADMITTED_VIA_BASELINE for i in identities)
    assert db_session.execute(select(SentinelSighting)).scalars().all() == []


# ---------------------------------------------------------------------------
# 7. first post-baseline new identity alerts


def test_first_post_baseline_new_identity_alerts(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    run_sweep(
        make_runner,
        notifier,
        force_baseline=True,
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN")])}),
    )
    run2 = run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map(
            {
                _TIMEX_US: _shopify_payload(
                    [("TW5M74100", "TIMEX IRONMAN"), ("TW5M74200", "TIMEX Weekender NEW")]
                )
            }
        ),
    )
    totals = run2.summary_metadata["totals"]
    assert totals["unseen_admitted"] == 1
    assert totals["known_suppressed"] == 1
    assert totals["alerts_sent"] == 1
    assert "TW5M74200" in notifier.calls[0]


# ---------------------------------------------------------------------------
# 8. source failure does not abort unrelated sources


def test_source_failure_isolates_sources(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()

    def boom():
        raise RuntimeError("connection reset")

    run = run_sweep(
        make_runner,
        notifier,
        fetch=_fetch_map(
            {
                _TIMEX_US: boom,
                _TIMEX_UK: _shopify_payload([("TW5M74200", "TIMEX Weekender")]),
            }
        ),
    )
    assert run.status == "PARTIAL"
    per_source = run.summary_metadata["per_source"]
    assert per_source["timex_us"]["status"] == "FAILED"
    # Timex UK still completed its poll (silently baselining on first success).
    assert per_source["timex_uk"]["status"] == "SUCCESS"
    assert per_source["timex_uk"]["baseline_mode"] is True
    assert run.summary_metadata["totals"]["alerts_sent"] == 0


# ---------------------------------------------------------------------------
# 9. parser handles representative Timex/Casio/Seiko/Citizen identities


def test_representative_identities():
    assert build_identity("Timex", reference_raw="TW5M74100", url="https://x").identity_key == "timex:tw5m74100"
    # Casio: hyphenated refs pass through; the JDM allowlist still applies
    # through the SHARED normalizer (the Sentinel invents no brand rules).
    assert build_identity("Casio", reference_raw="GWF-300", url="https://x").reference_canonical == "GWF-300"
    assert build_identity("Casio", reference_raw="GA-2100-1A1JF", url="https://x").reference_canonical == "GA-2100-1A1"
    assert build_identity("Seiko", reference_raw="SBXY105", url="https://x").reference_canonical == "SBXY105"
    assert build_identity("Citizen", reference_raw="AW1911-53A", url="https://x").identity_key == "citizen:aw1911-53a"
    # Regional suffix collapse: UK yes, colour suffix no, hyphenated never.
    assert collapse_regional_suffix("TW5M74100UK") == "TW5M74100"
    assert collapse_regional_suffix("TW6A01000VQ") == "TW6A01000VQ"
    assert collapse_regional_suffix("AW1911-53AUK") == "AW1911-53AUK"
    assert (
        build_identity("Timex", reference_raw="TW5M74100UK", url="https://x").identity_key
        != build_identity("Timex", reference_raw="TW5M74100VQ", url="https://x").identity_key
    )
    # Sitemap discovery delegates to the casio_uk collector's own parser.
    from app.sentinel.adapters import SitemapAdapter
    from app.sentinel.config import SENTINEL_SOURCES

    outcome = SitemapAdapter().poll(
        SENTINEL_SOURCES["casio_uk_sitemap"],
        fetch_fn=lambda url: FetchResult(url=url, success=True, status_code=200, payload=_CASIO_UK_SITEMAP),
    )
    assert sorted(c.reference_raw for c in outcome.candidates) == ["GA-2100-1A1", "GWF-A1000AP-1A"]


# ---------------------------------------------------------------------------
# 10. URL fallback works when no reference exists


def test_url_fallback_without_reference(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    fetch = _fetch_map({_TIMEX_US: _shopify_payload([(None, "TIMEX Mystery Limited Release")])})
    run = run_sweep(make_runner, notifier, only_source="timex_us", fetch=fetch)
    assert run.summary_metadata["totals"]["unseen_admitted"] == 1

    identity = (
        db_session.query(SentinelIdentity)
        .filter(SentinelIdentity.identity_type == "URL")
        .one()
    )
    assert identity.identity_key.startswith("timex:url:")
    assert identity.fallback_url == "https://www.timex.com/products/timex-mystery-limited-release"

    run2 = run_sweep(make_runner, notifier, only_source="timex_us", fetch=fetch)
    assert run2.summary_metadata["totals"]["unseen_admitted"] == 0


# ---------------------------------------------------------------------------
# 11. concurrent/overlapping execution cannot double-admit


def test_overlap_lock_prevents_second_sweep(make_runner, db_session, tmp_settings):  # noqa: F811 -- pytest fixture
    from app.services.run_lock import RunLockService

    lock = RunLockService(
        db_session,
        tmp_settings,
        collector_id="horology_sentinel",
        lock_path=tmp_settings.resolved_lock_path.parent / "horology_sentinel.run.lock",
    )
    assert lock.acquire().acquired
    run = run_sweep(
        make_runner,
        RecordingNotifier(),
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "X")])}),
    )
    assert run.status == "SKIPPED_OVERLAP"
    lock.release()


def test_racing_admission_cannot_double_alert(make_runner, db_session):  # noqa: F811 -- pytest fixture
    # Simulate another process admitting the identity between the sweep's
    # store snapshot and its admission: pre-seed the row, then sweep.
    now = datetime.now(UTC)
    db_session.add(
        SentinelIdentity(
            identity_key="timex:tw5m74100",
            identity_type="REFERENCE",
            manufacturer="Timex",
            reference_raw="TW5M74100",
            reference_canonical="TW5M74100",
            admitted_via="SIGHTING",
            first_source="elsewhere",
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db_session.commit()

    run = run_sweep(
        make_runner,
        RecordingNotifier(),
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN")])}),
    )
    # The source was unarmed (first poll = silent baseline), so nothing alerts.
    assert run.summary_metadata["totals"]["unseen_admitted"] == 0
    assert db_session.execute(select(SentinelSighting)).scalars().all() == []


# ---------------------------------------------------------------------------
# 12. Discord delivery follows the existing fleet contract


def test_delivery_receipt_and_idempotent_retry(make_runner, db_session, tmp_settings):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN")])}),
    )

    receipt = db_session.execute(select(DeliveryReceipt)).scalars().one()
    assert receipt.entity_type == ENTITY_SENTINEL_SIGHTING
    assert receipt.purpose == PURPOSE_SENTINEL_SIGHTING
    assert receipt.lifecycle_state == "PROVIDER_IDENTIFIED"
    assert receipt.provider_message_id == "m-1"
    assert receipt.destination_alias.startswith("editorial:")

    # A retried send for an already-accepted sighting must not re-ping.
    sighting = db_session.execute(select(SentinelSighting)).scalars().one()
    alerter = SentinelAlerter(db_session, tmp_settings, notifier=notifier)
    assert alerter.send(sighting) == "SENT"
    assert len(notifier.calls) == 1


def test_disabled_delivery_is_recorded_not_lost(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier(editorial_enabled=False)
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    run = run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN")])}),
    )
    totals = run.summary_metadata["totals"]
    assert totals["alerts_sent"] == 0
    assert totals["alerts_failed"] == 0
    sighting = db_session.execute(select(SentinelSighting)).scalars().one()
    assert sighting.alert_state == "DISABLED"
    assert sighting.identity_key == "timex:tw5m74100"


def test_failed_delivery_is_recorded_not_lost(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier(accepted=False)
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    run = run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN")])}),
    )
    assert run.summary_metadata["totals"]["alerts_failed"] == 1
    sighting = db_session.execute(select(SentinelSighting)).scalars().one()
    assert sighting.alert_state == "FAILED"


# ---------------------------------------------------------------------------
# 13. persisted identities survive restart


def test_identities_survive_restart(tmp_settings):  # noqa: F811 -- pytest fixture
    db_url = tmp_settings.resolved_database_url
    notifier = RecordingNotifier()
    seed_feed = _shopify_payload([("TW2Y99000", "Existing Timex")])
    model_feed = _shopify_payload([("TW5M74100", "TIMEX IRONMAN")])

    # "first process": its own engine + session against the tmp DB file.
    # Sweep 1 baselines the source; sweep 2 sights the model (now armed).
    engine1 = create_engine(db_url, future=True)
    Base.metadata.create_all(engine1)
    with sessionmaker(bind=engine1, expire_on_commit=False)() as s1:
        alerter1 = SentinelAlerter(s1, tmp_settings, notifier=notifier)
        SentinelRunner(s1, tmp_settings, fetch_fn=_fetch_map({_TIMEX_US: seed_feed}), alerter=alerter1).run_sweep(
            only_source="timex_us"
        )
        SentinelRunner(s1, tmp_settings, fetch_fn=_fetch_map({_TIMEX_US: model_feed}), alerter=alerter1).run_sweep(
            only_source="timex_us"
        )
    engine1.dispose()

    # "second process": brand-new engine/session against the same file.
    engine2 = create_engine(db_url, future=True)
    with sessionmaker(bind=engine2, expire_on_commit=False)() as s2:
        alerter2 = SentinelAlerter(s2, tmp_settings, notifier=notifier)
        run2 = SentinelRunner(s2, tmp_settings, fetch_fn=_fetch_map({_TIMEX_US: model_feed}), alerter=alerter2).run_sweep(
            only_source="timex_us"
        )
    engine2.dispose()

    assert run2.status == "SUCCESS"
    assert run2.summary_metadata["totals"]["known_suppressed"] == 1
    assert run2.summary_metadata["totals"]["alerts_sent"] == 0
    assert len(notifier.calls) == 1


# ---------------------------------------------------------------------------
# cadence tiers: sources are skipped until their cadence elapses


def test_cadence_due_logic(db_session):  # noqa: F811 -- pytest fixture
    from app.sentinel.store import SentinelStore

    store = SentinelStore(db_session)
    now = datetime.now(UTC)
    states = {name: store.source_state(name) for name in ("a", "b")}
    db_session.commit()

    # never-run sources are always due
    assert store.sources_due(states, {"a": 15, "b": 60}, now) == {"a", "b"}

    store.mark_poll(states["a"], ok=True, status="SUCCESS", now=now)
    assert "a" not in store.sources_due(states, {"a": 15, "b": 60}, now)
    assert "a" in store.sources_due(states, {"a": 15, "b": 60}, now + timedelta(minutes=16))
    # arming happened on first success
    assert states["a"].armed_at is not None


def test_zero_items_first_poll_does_not_arm(make_runner, db_session):  # noqa: F811 -- pytest fixture
    """Deployment finding 2026-09-09 (seiko_us live): a source whose first
    successful poll yields ZERO_ITEMS must NOT arm. Otherwise its real
    catalogue arrives later as 'unseen' and replays as a capped false-alert
    flood. The disarmed source silently baselines when candidates appear."""
    notifier = RecordingNotifier()
    # First poll: feed parses but the type filter matches nothing -> ZERO_ITEMS.
    empty_feed = _fetch_map({_TIMEX_US: _shopify_payload([])})
    run1 = run_sweep(make_runner, notifier, only_source="timex_us", fetch=empty_feed)
    assert run1.summary_metadata["per_source"]["timex_us"]["status"] == "ZERO_ITEMS"
    state = db_session.query(SentinelSourceState).filter_by(source="timex_us").one()
    assert state.armed_at is None  # NOT armed

    # Second poll: candidates appear. Still silent -- this is the source's
    # (late) baseline.
    feed = _fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN")])})
    run2 = run_sweep(make_runner, notifier, only_source="timex_us", fetch=feed)
    assert run2.summary_metadata["per_source"]["timex_us"]["baseline_mode"] is True
    assert run2.summary_metadata["totals"]["alerts_sent"] == 0

    # Third poll: armed now; a NEW identity alerts, the baselined one is
    # suppressed. No flood, ever.
    run3 = run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN"), ("TW5M74200", "TIMEX Weekender")])}),
    )
    assert run3.summary_metadata["totals"]["unseen_admitted"] == 1
    assert run3.summary_metadata["totals"]["known_suppressed"] == 1
    assert run3.summary_metadata["totals"]["alerts_sent"] == 1


# ---------------------------------------------------------------------------
# alert format contract


def test_alert_format_contract(make_runner, db_session):  # noqa: F811 -- pytest fixture
    notifier = RecordingNotifier()
    _arm(make_runner, notifier, "timex_us", _TIMEX_US, _shopify_payload([("TW2Y99000", "Existing Timex")]))
    run_sweep(
        make_runner,
        notifier,
        only_source="timex_us",
        fetch=_fetch_map({_TIMEX_US: _shopify_payload([("TW5M74100", "TIMEX IRONMAN Adrenaline 46.5mm")])}),
    )
    text = notifier.calls[0]
    assert text.startswith("**WATCH SIGHTING**")
    assert "Brand: Timex" in text
    assert "Model: TW5M74100" in text
    assert "Title: TIMEX IRONMAN Adrenaline 46.5mm" in text
    assert "Source: Timex US" in text
    assert "URL: https://www.timex.com/products/" in text
    assert "Sighting ID:" in text
    assert "UNENRICHED / FIRST-SEEN ONLY" in text
    # The sitemap lane has no title: the line is omitted, never invented.
    sighting = db_session.execute(select(SentinelSighting)).scalars().one()
    sighting.title = None
    assert "Title:" not in format_sighting(sighting)
