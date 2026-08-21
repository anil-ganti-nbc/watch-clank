from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    ROOT / "tests/fixtures/casio_intl_news_efk200.html",
    ROOT / "tests/fixtures/casio_intl_news_list.html",
)


def test_casio_fixture_telemetry_values_are_inert() -> None:
    for fixture in FIXTURES:
        text = fixture.read_text(encoding="utf-8")
        assert 'window.BOOMR_API_key="CLANK_FIXTURE_BOOMR_KEY"' in text


def test_gitleaks_still_detects_a_credential_shaped_value(tmp_path: Path) -> None:
    scanner = shutil.which("gitleaks")
    if scanner is None:
        pytest.skip("gitleaks is installed only in the security job")
    synthetic_key = "aB3d" + "E7gH1jK5mN9qR2tV6xY8zC4pL7sW"
    (tmp_path / "credential.txt").write_text(f"api_key={synthetic_key}\n")
    result = subprocess.run(
        [scanner, "dir", "--redact=100", "--no-banner", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
