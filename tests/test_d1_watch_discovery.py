"""Synthetic, test-only documents: not source qualification or launch evidence."""
import json
import os
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import discovery as d


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Network prohibited in Discovery tests')
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    evidence = tmp_path / 'producer-evidence'
    evidence.mkdir()
    cfg = d.Settings(d.PRODUCER_ID, tmp_path / 'd1.py', tmp_path / 'profiles.json',
                     evidence, tmp_path / 'consumer', True)
    source = {'id': 'watches', 'publisher': 'MONOCHROME Watches',
              'description': 'Synthetic test-only descriptor',
              'url': 'https://monochrome-watches.com/feed/'}
    profile = {'id': 'watches', 'destination': d.DESTINATION, 'sources': [source]}
    module = SimpleNamespace(validate_structure=lambda p: None, validate=lambda p: None)
    monkeypatch.setattr(d, 'authenticate', lambda cfg: (module, {'schema_version': 'd1.profiles.v1', 'profiles': [profile]}))
    passage = 'Synthetic test-only mechanical watch introduced.'
    text = evidence / 'text.txt'
    capture = evidence / 'capture.xml'
    text.write_text(passage, encoding='utf8')
    capture.write_bytes(b'<synthetic-test-only/>')
    p = {'lead_id': 'synthetic-lead', 'category_profile': 'watches',
             'suggested_consumer': d.DESTINATION, 'source_id': 'watches',
             'publisher': source['publisher'], 'source_descriptor': source['description'],
             'source_title': 'Synthetic watch introduced', 'subject_text': 'Synthetic watch introduced',
             'source_url': 'https://example.org/test-only', 'supporting_passage': passage,
             'media_metadata': [], 'content_source_field': 'description',
             'decision': 'main', 'event_hint': 'announcement_candidate', 'review_state': 'unreviewed',
             'uncertainties': ['Synthetic fixture; no source qualification'],
             'source_published_at': '2020-01-01T00:00:00Z',
             'source_published_at_original': '2020-01-01T00:00:00Z',
             'observed_at': '2020-01-02T00:00:00Z', 'collected_at': '2020-01-02T00:00:00Z',
             'first_observed_at': '2020-01-02T00:00:00Z',
             'evidence': {'text_file': str(text), 'capture_file': str(capture),
                           'text_sha256': d.digest(text.read_bytes()),
                           'capture_sha256': d.digest(capture.read_bytes()), 'offset': 0,
                           'length': len(passage.encode())}}
    export = tmp_path / 'synthetic.jsonl'
    export.write_text(json.dumps(p) + '\n', encoding='utf8')
    return cfg, p, export


def test_generation_dedup_review_change_omission_and_disable(synthetic):
    cfg, p, export = synthetic
    assert d.import_export(cfg, export)['inserted'] == 1
    d.review(cfg, p['lead_id'], 'editorial_keep')
    assert d.import_export(cfg, export)['unchanged'] == 1
    p['uncertainties'].append('Changed synthetic observation')
    export.write_text(json.dumps(p), encoding='utf8')
    assert d.import_export(cfg, export)['changed'] == 1
    export.write_text('', encoding='utf8')
    d.import_export(cfg, export)
    row, = d.read_inbox(replace(cfg, enabled=False))
    assert row['consumer_review'] == 'editorial_keep'
    assert len(row['history']) == 2
    assert row['payload']['source_published_at'].startswith('2020-')
    with pytest.raises(ValueError, match='disabled'):
        d.import_export(replace(cfg, enabled=False), export)
    for file in cfg.evidence_root.iterdir():
        file.unlink()
    assert d.evidence_bytes(cfg, next(iter(row['copied'].values())))


def test_malformed_and_missing_evidence(synthetic):
    cfg, p, export = synthetic
    d.import_export(cfg, export)
    before = cfg.generation
    export.write_text('{broken', encoding='utf8')
    with pytest.raises(ValueError):
        d.import_export(cfg, export)
    assert cfg.generation == before
    Path(p['evidence']['capture_file']).unlink()
    export.write_text(json.dumps(p), encoding='utf8')
    d.import_export(cfg, export)
    assert d.read_inbox(cfg)[0]['evidence_status'] == 'unavailable/quarantined'


def test_consumer_boundary_excludes_not_research(synthetic):
    cfg, p, export = synthetic
    p['source_title'] = p['subject_text'] = 'Synthetic smartwatch introduced'
    export.write_text(json.dumps(p), encoding='utf8')
    result = d.import_export(cfg, export)
    assert result['accepted'] == 0
    assert sum(result['excluded'].values()) == 1
    assert d.read_inbox(cfg) == []


def test_dashboard_escaped_uncapped_and_reserved_port(synthetic):
    from app.discovery_dashboard import create_server, render_discovery
    cfg, p, export = synthetic
    p['source_title'] = p['subject_text'] = '<script>alert(1)</script> watch'
    export.write_text(json.dumps(p), encoding='utf8')
    d.import_export(cfg, export)
    rendered = render_discovery(cfg)
    assert 'Watch Clank' in rendered
    assert '<script>' not in rendered
    assert '&lt;script&gt;' in rendered
    assert 'unresolved' in rendered and 'Copied text' in rendered
    with pytest.raises(ValueError):
        create_server(cfg, 8765)


def test_cli_help_and_early_launcher():
    result = subprocess.run([sys.executable, '-m', 'app.discovery_cli', '--help'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert 'configure' in result.stdout and '--settings' in result.stdout
    code = "import sys; import app.serve; assert 'app.main' not in sys.modules; assert 'app.core.config' not in sys.modules"
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_refresh_stages_authenticated_projection_and_retains_failure(synthetic, monkeypatch):
    cfg, _, _ = synthetic
    module, profiles = d.authenticate(cfg)
    module.load_config = lambda path: [(profiles['profiles'][0], profiles['profiles'][0]['sources'][0])]
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['timeout'] <= 90
        projected = d.strict_json(Path(argv[argv.index('--config') + 1]).read_bytes())
        assert [p['id'] for p in projected['profiles']] == ['watches']
        state = Path(argv[argv.index('--state') + 1])
        (state / 'leads.jsonl').write_text('', encoding='utf8')
        return subprocess.CompletedProcess(argv, 0, d.dumps({'status': 'COMPLETE', 'network_requests': 1, 'network_bytes': 100}), '')
    monkeypatch.setattr(d.subprocess, 'run', fake_run)
    assert d.refresh(cfg)['status'] == 'IMPORTED'
    before = cfg.generation
    def failed(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 2, '{"status":"FAILED"}', 'synthetic failure')
    monkeypatch.setattr(d.subprocess, 'run', failed)
    with pytest.raises(ValueError):
        d.refresh(cfg)
    assert cfg.generation == before
    stages = list((cfg.data_dir / 'refresh-staging').iterdir())
    assert len(stages) == 2
    assert all((s / 'stdout.txt').exists() and (s / 'execution.json').exists() for s in stages)


def selected_d1_root():
    supplied = os.environ.get('WATCH_CLANK_D1_ROOT')
    if supplied is None:
        pytest.skip('WATCH_CLANK_D1_ROOT not supplied: accepted external D1 inputs absent')
    root = Path(supplied)
    if not supplied or not root.is_absolute() or not root.is_dir():
        pytest.fail('WATCH_CLANK_D1_ROOT must select an existing absolute directory')
    for name in ('d1.py', 'profiles.json'):
        if not (root / name).is_file():
            pytest.fail('WATCH_CLANK_D1_ROOT selected file missing or not regular: ' + name)
    return root


def test_accepted_external_auth_projection_and_synthetic_replay(tmp_path):
    root = selected_d1_root()
    evidence = tmp_path / 'state'
    evidence.mkdir()
    cfg = d.Settings(d.PRODUCER_ID, root / 'd1.py', root / 'profiles.json', evidence, tmp_path / 'consumer', True)
    module, original = d.authenticate(cfg)
    projected = d.project_profiles(original)
    assert [p['id'] for p in projected['profiles']] == ['watches']
    projection = tmp_path / 'projection.json'
    projection.write_text(d.dumps(projected), encoding='utf8')
    assert len(module.load_config(projection)) == 1
    assert projected['profiles'][0] == next(p for p in original['profiles'] if p['id'] == 'watches')
    assert projected['profiles'][0]['destination'] == 'watches-clank:proposal-review'
    source, = projected['profiles'][0]['sources']
    assert source['id'] == 'watches'
    assert source['url'] == 'https://monochrome-watches.com/feed/'
    assert source['publisher'] == 'MONOCHROME Watches'
    assert {p['id'] for p in original['profiles']} == {'computing', 'watches'}
    raw = b'<rss version="2.0"><channel><item><title>Synthetic example watch introduced</title><link>https://example.org/synthetic-watch</link><pubDate>Wed, 01 Jan 2020 00:00:00 GMT</pubDate><description>Synthetic mechanical watch movement introduced for testing only.</description></item></channel></rss>'
    capture = evidence / 'capture.xml'
    capture.write_bytes(raw)
    doc, = module.parse_feed(raw)
    db = module.connect(evidence)
    try:
        profile = projected['profiles'][0]
        p = module.proposal(db, evidence, profile, profile['sources'][0], doc,
                            {'capture_file': str(capture), 'capture_sha256': d.digest(raw),
                             'response_url': profile['sources'][0]['url']}, '2020-01-02T00:00:00Z')
    finally:
        db.close()
    export = tmp_path / 'export.jsonl'
    export.write_text(d.dumps(p), encoding='utf8')
    assert d.import_export(cfg, export)['inserted'] == 1
    assert d.import_export(cfg, export)['unchanged'] == 1
    assert d.read_inbox(cfg)[0]['payload']['brand'] is None


@pytest.mark.parametrize('title', [
    # Exact frozen authentic title supplied in parent R1; no private capture.
    'Introducing The XBAND Smart Strap That Does the Smart Work for Your Mechanical Watch',
    'Introducing an Example smart strap for your mechanical watch',
    'Introducing a replacement watch strap for a chronograph',
    'Example steel bracelet introduced for your watch',
    'Example watch winder introduced',
    'Example watch box introduced',
    'Example watch roll introduced',
    'Synthetic smartwatch introduced',
    'Example fitness tracker introduced',
    'Example tablet introduced',
    'Example watch discount coupon',
])
def test_accessory_primary_and_existing_negatives_excluded(synthetic, title):
    cfg, p, export = synthetic
    p['source_title'] = p['subject_text'] = title
    export.write_text(json.dumps(p), encoding='utf8')
    assert d.routing_reason(p) is not None
    result = d.import_export(cfg, export)
    assert result['accepted'] == 0
    assert sum(result['excluded'].values()) == 1
    assert d.read_inbox(cfg) == []  # excluded, never hidden in research


@pytest.mark.parametrize('title', [
    'Example chronograph watch introduced with a steel bracelet',
    'Introducing the Example mechanical watch with included straps',
    'Example timepiece introduced on a leather watch strap',
    'Example digital watch introduced with a rubber watch band',
    'Introducing the Example chronograph and its steel bracelet',
    'Example mechanical watch introduced',
])
def test_watch_primary_accessory_attributes_retained(synthetic, title):
    cfg, p, export = synthetic
    p['source_title'] = p['subject_text'] = title
    export.write_text(json.dumps(p), encoding='utf8')
    assert d.routing_reason(p) is None
    assert d.import_export(cfg, export)['accepted'] == 1
    row, = d.read_inbox(cfg)
    assert row['payload']['decision'] == 'main'
    assert row['payload']['source_title'] == title


def test_external_selector_absent_explicit_skip(monkeypatch):
    monkeypatch.delenv('WATCH_CLANK_D1_ROOT', raising=False)
    with pytest.raises(pytest.skip.Exception, match='not supplied'):
        selected_d1_root()


@pytest.mark.parametrize('binding', ['absent-root', 'root-file', 'empty-root', 'missing-profiles', 'missing-producer', 'producer-directory', 'relative-root', 'empty-value'])
def test_external_selector_supplied_bad_binding_fails(tmp_path, monkeypatch, binding):
    root = tmp_path / 'selected'
    if binding == 'root-file':
        root.write_text('not a directory', encoding='utf8')
    elif binding != 'absent-root':
        root.mkdir()
        if binding in ('missing-profiles', 'producer-directory'):
            (root / 'd1.py').mkdir() if binding == 'producer-directory' else (root / 'd1.py').write_text('synthetic invalid producer', encoding='utf8')
        if binding in ('missing-producer', 'producer-directory'):
            (root / 'profiles.json').write_text('{}', encoding='utf8')
    value = '' if binding == 'empty-value' else ('relative-root' if binding == 'relative-root' else str(root))
    monkeypatch.setenv('WATCH_CLANK_D1_ROOT', value)
    with pytest.raises(pytest.fail.Exception, match='WATCH_CLANK_D1_ROOT'):
        selected_d1_root()


def test_external_selector_valid_files_do_not_waive_authentication(tmp_path, monkeypatch):
    root = tmp_path / 'selected'
    root.mkdir()
    (root / 'd1.py').write_text('raise AssertionError("must never execute unpinned code")', encoding='utf8')
    (root / 'profiles.json').write_text('{}', encoding='utf8')
    monkeypatch.setenv('WATCH_CLANK_D1_ROOT', str(root))
    assert selected_d1_root() == root
    cfg = d.Settings(d.PRODUCER_ID, root / 'd1.py', root / 'profiles.json',
                     tmp_path / 'evidence', tmp_path / 'consumer', True)
    with pytest.raises(ValueError, match='pin mismatch'):
        d.authenticate(cfg)
    assert not cfg.data_dir.exists()


@pytest.mark.parametrize('changed', ['d1.py', 'profiles.json'])
def test_external_selected_tampered_binding_refused(tmp_path, monkeypatch, changed):
    accepted = selected_d1_root()
    root = tmp_path / 'selected'
    root.mkdir()
    for name in ('d1.py', 'profiles.json'):
        raw = (accepted / name).read_bytes()
        (root / name).write_bytes(raw + (b'\n' if name == changed else b''))
    monkeypatch.setenv('WATCH_CLANK_D1_ROOT', str(root))
    assert selected_d1_root() == root
    cfg = d.Settings(d.PRODUCER_ID, root / 'd1.py', root / 'profiles.json',
                     tmp_path / 'evidence', tmp_path / 'consumer', True)
    with pytest.raises(ValueError, match='pin mismatch'):
        d.authenticate(cfg)
    assert not cfg.data_dir.exists()


