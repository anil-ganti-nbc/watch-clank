"""Production-wiring invariants for registered collectors.

2026-08-25 deployment finding: tissot_sitemap and timex_uk_products existed
as collector implementations and pipeline-registry entries but were absent
from the PRODUCTION invocation chain (health.KNOWN_COLLECTORS ->
collector_registry._CONTROLS -> render_units.py / run_pipeline.py argparse),
so the systemd renderer could not create timers for them. The canonical
chain is: KNOWN_COLLECTORS is the single source of truth;
collector_registry._CONTROLS maps each id to its exact run_pipeline CLI
invocation; render_units.py derives systemd units from _CONTROLS.

INVARIANT (owner-mandated): every collector marked
EXPERIMENTAL_READY_FOR_HETZNER in WATCH_SOAK_CONTRACT.md must be
mechanically invokable through the same production path used by the
canonical scheduler. These tests fail if a registered experimental
collector lacks a production entrypoint.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _controls():
    from app.services.collector_registry import all_controls

    return all_controls()


def test_expansion_collectors_are_in_known_collectors():
    """Single source of truth: without KNOWN_COLLECTORS membership there is
    no health surface, no individual RUN NOW control, no unit rendering.
    (KNOWN_COLLECTORS membership no longer implies RUN ALL/SAFE_COLLECTOR_IDS
    inclusion -- see test_run_all_excludes_experimental_maturity_by_default
    below, the 2026-08-27 eligibility-gate closeout.)"""
    from app.services.health import KNOWN_COLLECTORS

    assert "tissot_sitemap" in KNOWN_COLLECTORS
    assert "timex_uk_products" in KNOWN_COLLECTORS


def test_expansion_collectors_have_production_cli_controls():
    controls = {c.collector_id: c for c in _controls()}
    assert "tissot_sitemap" in controls
    assert "timex_uk_products" in controls
    assert controls["tissot_sitemap"].cli_args == ("--experimental-product", "tissot")
    assert controls["timex_uk_products"].cli_args == ("--experimental-product", "timex_uk")


def test_run_pipeline_accepts_both_brands_via_argparse():
    """The exact production invocation path must accept the brand choices."""
    import subprocess
    import sys

    for brand in ("tissot", "timex_uk"):
        # --help exercises argparse choices validation without running anything.
        result = subprocess.run(
            [sys.executable, "-m", "scripts.run_pipeline", "--experimental-product", brand,
             "--force-baseline", "--max-items", "1", "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
        # argparse choices must ACCEPT both brands (rc==2 means invalid choice).
        assert result.returncode != 2, f"{brand} rejected by production CLI choices"
        assert "--experimental-product: invalid choice" not in result.stderr


def test_render_units_emits_canonical_systemd_units_for_both(tmp_path):
    """The deployment blocker's direct regression test: the renderer must
    emit .service + .timer for every registered collector, with the right
    ExecStart args and cadence."""
    from scripts.systemd.docker.render_units import render

    written = render(tmp_path)
    names = [Path(w).name for w in written]
    joined = "\n".join(written)

    assert "watch-clank-tissot-sitemap.service" in names
    assert "watch-clank-tissot-sitemap.timer" in names
    assert "watch-clank-timex-uk-products.service" in names
    assert "watch-clank-timex-uk-products.timer" in names

    service = (tmp_path / "watch-clank-tissot-sitemap.service").read_text()
    assert "--experimental-product tissot" in service

    timer = (tmp_path / "watch-clank-timex-uk-products.timer").read_text()
    assert "21600" in timer  # 360 min * 60 s per EXPECTED_CADENCE_MINUTES

    assert "tissot-sitemap" in joined and "timex-uk-products" in joined


def test_seiko_jp_products_timer_carries_scheduled_provenance(tmp_path):
    """2026-09-03 incident regression (Seiko JP delivery-gating failure):
    the rendered production unit for seiko_jp_products must explicitly
    declare --qualification-provenance SCHEDULED, or the qualification
    service fails closed (eligibility_gate=UNKNOWN) and silently blocks
    Discord delivery for every eligible event -- exactly what happened to
    ten confirmed-eligible NEW_REFERENCE events on 2026-09-02. It must also
    still invoke the same --experimental-product seiko_jp entrypoint; this
    fix changes the provenance declared to that entrypoint, not the
    entrypoint or collector identity itself."""
    from scripts.systemd.docker.render_units import render

    render(tmp_path)
    service = (tmp_path / "watch-clank-seiko-jp-products.service").read_text()

    assert "--experimental-product seiko_jp" in service
    assert "--qualification-provenance SCHEDULED" in service


def test_every_non_casio_multi_collector_timer_carries_scheduled_provenance(tmp_path):
    """Narrow fleet-wide audit requested alongside the Seiko JP fix: every
    OTHER systemd-scheduled collector uses the same --experimental-* entry
    points, whose argparse default for --qualification-provenance is
    UNKNOWN (unlike --scheduled, which already defaults it correctly) --
    see scripts/run_pipeline.py. Confirmed affected set before this fix:
    every registered collector except casio_multi. This test fails if a
    future collector is added to the registry without carrying truthful
    scheduled provenance into its rendered timer invocation."""
    from app.services.collector_registry import all_controls
    from scripts.systemd.docker.render_units import render, slug_for

    render(tmp_path)

    for control in all_controls():
        service_path = tmp_path / f"watch-clank-{slug_for(control.collector_id)}.service"
        service = service_path.read_text()
        if control.collector_id == "casio_multi":
            assert "--scheduled" in service
            continue
        assert "--qualification-provenance SCHEDULED" in service, (
            f"{control.collector_id}'s rendered timer invocation is missing truthful "
            "scheduled provenance and will fail closed on delivery"
        )


def test_registered_collector_without_production_entrypoint_fails_loudly():
    """THE invariant: any id in KNOWN_COLLECTORS lacking a _CONTROLS entry
    (or vice versa) breaks the production chain. The registry validates this
    at import time; simulate drift by patching KNOWN_COLLECTORS and
    re-executing the validation block directly."""
    from unittest.mock import patch

    import app.services.collector_registry as reg

    source = Path(reg.__file__).read_text(encoding="utf-8")
    validation_block = source[source.index("_missing_controls = set("):source.index('# "RUN ALL SAFE COLLECTORS"')]

    with patch.object(reg, "KNOWN_COLLECTORS", [*reg.KNOWN_COLLECTORS[:-1]]):
        # Re-run the module-level sync guard against drifted input: the last
        # KNOWN_COLLECTORS entry (timex_uk_products) is removed, so the
        # guard MUST raise rather than let an unregistered control through.
        import pytest

        with pytest.raises(RuntimeError):
            exec(compile(validation_block, "<registry-sync-guard>", "exec"), {"KNOWN_COLLECTORS": reg.KNOWN_COLLECTORS[:-1], "_CONTROLS": reg._CONTROLS})  # noqa: S102


def test_soak_contract_experimental_set_matches_registry_controls():
    """Every collector marked EXPERIMENTAL_READY_FOR_HETZNER in the soak
    contract must be mechanically invokable through the production path.
    This keeps the DOCUMENT and the CODE from drifting apart."""
    contract = (ROOT / "WATCH_SOAK_CONTRACT.md").read_text(encoding="utf-8")
    controls = {c.collector_id for c in _controls()}

    for cid in ("tissot_sitemap", "timex_uk_products"):
        assert cid in contract, f"{cid} missing from soak contract"
        assert cid in controls, f"{cid} lacks a production invocation control"


def test_run_all_excludes_experimental_maturity_by_default():
    """2026-08-27 operator closeout decision: EXPERIMENTAL-maturity
    collectors must be excluded from RUN ALL/SAFE_COLLECTOR_IDS at the
    eligibility layer itself, independent of delivery_gate's external-
    delivery silence (the operator's explicit instruction: "Do not rely on
    delivery gating as the UI eligibility mechanism"). They stay fully
    wired for individual RUN NOW/COLLECT -- only bulk-run membership
    changes."""
    from app.services.collector_registry import SAFE_COLLECTOR_IDS, all_controls
    from app.services.delivery_gate import EXPERIMENTAL_MATURITY_COLLECTORS
    from app.services.health import KNOWN_COLLECTORS

    for cid in EXPERIMENTAL_MATURITY_COLLECTORS:
        assert cid in KNOWN_COLLECTORS, f"{cid} should still be a known/health-tracked collector"
        assert cid in {c.collector_id for c in all_controls()}, (
            f"{cid} must remain individually runnable via RUN NOW/COLLECT"
        )
        assert cid not in SAFE_COLLECTOR_IDS, (
            f"{cid} is EXPERIMENTAL maturity and must not be swept into RUN ALL by default"
        )

    # Every non-experimental known collector is still eligible by default --
    # this gate only removes the soak set, nothing else.
    assert set(KNOWN_COLLECTORS) - EXPERIMENTAL_MATURITY_COLLECTORS == set(SAFE_COLLECTOR_IDS)


def test_run_all_experimental_reenable_flag_restores_full_set(monkeypatch):
    """Config-driven re-enable: WATCH_CLANK_RUN_ALL_INCLUDE_EXPERIMENTAL=1
    folds the current EXPERIMENTAL_MATURITY_COLLECTORS set back into RUN
    ALL eligibility -- a deliberate operator opt-in, off by default."""
    from app.services.collector_registry import _resolve_safe_collector_ids
    from app.services.health import KNOWN_COLLECTORS

    monkeypatch.setenv("WATCH_CLANK_RUN_ALL_INCLUDE_EXPERIMENTAL", "1")
    assert set(_resolve_safe_collector_ids()) == set(KNOWN_COLLECTORS)

    monkeypatch.delenv("WATCH_CLANK_RUN_ALL_INCLUDE_EXPERIMENTAL", raising=False)
    from app.services.delivery_gate import EXPERIMENTAL_MATURITY_COLLECTORS

    assert set(_resolve_safe_collector_ids()) == set(KNOWN_COLLECTORS) - EXPERIMENTAL_MATURITY_COLLECTORS
