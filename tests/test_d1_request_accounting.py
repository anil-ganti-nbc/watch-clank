"""Offline mocked wire accounting, NOT reconstructed historical request receipts."""
import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import discovery as d


@pytest.fixture
def transport_fixture(tmp_path, monkeypatch):
    root = os.environ.get('WATCH_CLANK_D1_ROOT')
    if root is None:
        pytest.skip('WATCH_CLANK_D1_ROOT absent: original external D1 accounting not exercised')
    root = Path(root)
    assert root.is_absolute() and root.is_dir(), 'Invalid explicit D1 root'
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    cfg = d.Settings(d.PRODUCER_ID, root / 'd1.py', root / 'profiles.json',
                     evidence, tmp_path / 'consumer', True)
    module, original = d.authenticate(cfg)
    assert len(d.project_profiles(original)['profiles'][0]['sources']) == 1
    def denied(*args, **kwargs):
        raise AssertionError('Accounting contrasts prohibit real networking')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    # These tests exercise accounting, not URL admission/DNS, already qualified separately.
    monkeypatch.setattr(module, 'safe_url', lambda value, feed=False: value)
    monkeypatch.setattr(module.ssl, 'create_default_context', lambda: None)
    transport = module.Transport()
    attempts = []
    statuses = []
    class Response:
        def __init__(self, status):
            self.status = status
        def getheader(self, name):
            return '/feed/?offline-hop=next' if name == 'Location' else None
        def read(self, size):
            return b''
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def request(self, method, target, **kwargs):
            # Captured at the attempted-wire boundary, before any response is known.
            attempts.append(transport.requests)
            if statuses and statuses[0] == 'request-error':
                statuses.pop(0)
                raise OSError('offline request failure')
        def getresponse(self):
            return Response(statuses.pop(0))
        def close(self):
            pass
    monkeypatch.setattr(module, 'PinnedHTTPS', Connection)
    return module, transport, attempts, statuses


def test_each_redirect_consumes_before_attempt(transport_fixture):
    _, transport, attempts, statuses = transport_fixture
    statuses.extend([302, 200])
    _, _, status, chain = transport.get_inline('https://monochrome-watches.com/feed/')
    assert status == 200 and len(chain) == 2
    assert attempts == [1, 2] and transport.requests == 2


def test_redirect_crossing_remaining_allowance_refuses_next_attempt(transport_fixture):
    _, transport, attempts, statuses = transport_fixture
    transport.requests = 11  # Existing accepted transport has an aggregate ceiling of12.
    statuses.extend([302, 200])
    with pytest.raises(ValueError, match='request budget'):
        transport.get_inline('https://monochrome-watches.com/feed/')
    assert attempts == [12] and transport.requests == 12
    assert statuses == [200]  # No extra request to the redirect target.


def test_one_fetch_redirect_chain_cannot_exceed_three_attempts(transport_fixture):
    _, transport, attempts, statuses = transport_fixture
    statuses.extend([302, 302, 302, 200])
    with pytest.raises(ValueError, match='Redirect limit'):
        transport.get_inline('https://monochrome-watches.com/feed/')
    assert attempts == [1, 2, 3] and transport.requests == 3
    assert statuses == [200]


def test_explicit_retry_cannot_replenish_consumed_allowance(transport_fixture):
    _, transport, attempts, statuses = transport_fixture
    transport.requests = 11
    statuses.extend([503, 200])
    assert transport.get_inline('https://monochrome-watches.com/feed/')[2] == 503
    with pytest.raises(ValueError, match='request budget'):
        transport.get_inline('https://monochrome-watches.com/feed/')
    assert attempts == [12] and transport.requests == 12 and statuses == [200]


def test_failed_attempt_is_charged_before_retry_refusal(transport_fixture):
    _, transport, attempts, statuses = transport_fixture
    transport.requests = 11
    statuses.extend(['request-error', 200])
    with pytest.raises(OSError, match='offline request failure'):
        transport.get_inline('https://monochrome-watches.com/feed/')
    with pytest.raises(ValueError, match='request budget'):
        transport.get_inline('https://monochrome-watches.com/feed/')
    assert attempts == [12] and transport.requests == 12 and statuses == [200]


def test_parent_aggregates_child_wire_count_not_fetch_calls(transport_fixture, monkeypatch):
    module, transport, _, _ = transport_fixture
    result = {'requests': 3, 'bytes': 0, 'body': '', 'url':
              'https://monochrome-watches.com/feed/', 'status': 200, 'chain': []}
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=json.dumps(result)))
    transport.get('https://monochrome-watches.com/feed/')
    assert transport.requests == 3  # One get(), three attempted HTTP hops.


def test_parent_preserves_count_even_when_worker_reports_failure(transport_fixture, monkeypatch):
    module, transport, _, _ = transport_fixture
    result = {'requests': 2, 'bytes': 0, 'error': 'offline failed fetch'}
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout=json.dumps(result)))
    with pytest.raises(ValueError, match='offline failed fetch'):
        transport.get('https://monochrome-watches.com/feed/')
    assert transport.requests == 2


def test_worker_timeout_marks_accounting_unknown_not_success(transport_fixture, monkeypatch):
    module, transport, _, _ = transport_fixture
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 8)
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        transport.get('https://monochrome-watches.com/feed/')
    assert transport.incomplete_accounting is True
