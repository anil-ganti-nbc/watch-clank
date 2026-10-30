"""Discovery-only loopback GET view; no catalogue imports or legacy routes.

Evidence and all-observation rendering adapted from OEM Radar merge
f5a2379e0a2953a471850785648b7e058970cb2a.
"""
import json
import re
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import discovery as d


def evidence_bytes(cfg, name):
    if not re.fullmatch(r'[a-f0-9]{64}', name):
        raise ValueError('Invalid evidence identifier')
    allowed = set()
    for row in d.read_inbox(cfg):
        for observation in row['history']:
            allowed.update(json.loads(observation['copied']).values())
    if name not in allowed:
        raise ValueError('Unreferenced evidence')
    path = d.checked_path(cfg.data_dir / 'evidence', str(cfg.data_dir / 'evidence' / name))
    raw = d.read_bounded(path, d.MAX_FILE)
    if d.digest(raw) != name:
        raise ValueError('Owned evidence corruption')
    return raw


def render_discovery(cfg):
    def h(value):
        return escape(str(value), quote=True)
    rows = d.read_inbox(cfg)
    counts = {label: sum(r['payload']['decision'] == label for r in rows) for label in ('main', 'research')}
    counts['unresolved'] = sum(r['payload']['event_hint'] == 'unresolved' or r['evidence_status'] != 'verified' for r in rows)
    reviews = {label: sum(r['consumer_review'] == label for r in rows) for label in d.LABELS}
    cards = []
    for row in rows:
        p = row['payload']
        url = p['source_url']
        parsed = urlparse(url)
        title = h(p['source_title'])
        if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
            title = '<a rel="noopener noreferrer" href="' + h(url) + '">' + title + '</a>'
        clocks = ''.join('<dt>' + h(key) + '</dt><dd>' + h(p[key]) + '</dd>' for key in ('source_published_at', 'source_published_at_original', 'observed_at', 'collected_at', 'first_observed_at'))
        history = []
        for observation in row['history']:
            copied = json.loads(observation['copied'])
            links = ' · '.join('<a href="/discovery/evidence/' + h(name) + '">Copied ' + h(kind) + '</a>' for kind, name in copied.items())
            history.append('<details><summary>Observation ' + h(observation['id']) + ' · ' + h(observation['imported_at']) + ' · ' + h(observation['evidence_status']) + '</summary>' + links + '<pre>' + h(json.dumps(dict(observation), ensure_ascii=False, indent=2)) + '</pre></details>')
        cards.append('<article><h2>' + title + '</h2><p>Third-party proposal/report — not product truth, admission or official evidence.</p><p>Source URL: ' + h(url) + '</p><blockquote>' + h(p['supporting_passage']) + '</blockquote><p>Publisher: ' + h(p['publisher']) + ' · ' + h(p['source_descriptor']) + '</p><dl>' + clocks + '<dt>Imported at</dt><dd>' + h(row['imported_at']) + '</dd><dt>Producer queue / review</dt><dd>' + h(p['decision']) + ' / ' + h(p['review_state']) + '</dd><dt>Consumer editorial review</dt><dd>' + h(row['consumer_review']) + '</dd><dt>Evidence</dt><dd>' + h(row['evidence_status']) + ' ' + h(row['limitations']) + '</dd><dt>Uncertainties</dt><dd>' + h('; '.join(p['uncertainties'])) + '</dd></dl><p>' + h(row['producer_id']) + ' / ' + h(p['lead_id']) + '</p><h3>Change history and copied evidence</h3>' + ''.join(history) + '<details><summary>Editorial review history</summary><pre>' + h(json.dumps(row['review_history'], indent=2)) + '</pre></details></article>')
    return '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Watch Clank · Discovery</title><style>body{font:16px system-ui;max-width:1000px;margin:auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}article{padding:20px;margin:16px 0;border:1px solid #777}dd{overflow-wrap:anywhere}</style></head><body><main><h1>Discovery · proposals only</h1><p>' + str(len(rows)) + ' retained proposals · all retained leads shown (no cap)</p><p>Enabled: ' + h(cfg.enabled) + ' (viewing never collects)</p><p>Queues (unresolved overlaps): ' + h(d.dumps(counts)) + '</p><p>Consumer review: ' + h(d.dumps(reviews)) + '</p><a href="/api/discovery">All retained proposals and history (JSON)</a>' + ''.join(cards) + '</main></body></html>'


def dashboard_handler(cfg, db_path=None):
    if db_path is not None:
        raise ValueError('Discovery never accepts a catalogue path')

    class DiscoveryHandler(BaseHTTPRequestHandler):
        def _send(self, status, body, ctype):
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" + ('; sandbox' if ctype.startswith('text/plain') else ''))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_error(405, 'Discovery is GET-only; review uses explicit CLI')

        do_PUT = do_POST  # noqa: N815 - stdlib handler protocol
        do_DELETE = do_POST  # noqa: N815
        do_PATCH = do_POST  # noqa: N815

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path.startswith('/discovery/evidence/'):
                    try:
                        raw = evidence_bytes(cfg, path.removeprefix('/discovery/evidence/'))
                    except (ValueError, OSError):
                        self.send_error(404)
                        return
                    self._send(200, raw, 'text/plain; charset=utf-8')
                elif path in ('/', '/discovery'):
                    self._send(200, render_discovery(cfg).encode('utf8'), 'text/html; charset=utf-8')
                elif path == '/api/discovery':
                    self._send(200, d.dumps(d.read_inbox(cfg)).encode('utf8'), 'application/json')
                elif path == '/health':
                    self._send(200, b'{"status":"OK","mode":"discovery-only"}', 'application/json')
                else:
                    self.send_error(404)
            except Exception:
                self.send_error(500, 'Discovery read failed')

    return DiscoveryHandler


def create_server(cfg, port=8790):
    if not 0 <= port <= 65535 or port in (8765, 8787):
        raise ValueError('Invalid or reserved Discovery port')
    return ThreadingHTTPServer(('127.0.0.1', port), dashboard_handler(cfg))


def serve(cfg, port=8790):
    server = create_server(cfg, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
