"""Manual D1 proposal intake. No catalogue, crawler or notification dependencies.

CURRENT is the atomic publication point for a sidecar/export generation. Readers
never create files. Producer code is executed only after fixed hash authentication.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import types
import uuid
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

# Adapted from OEM Radar f5a2379e0a2953a471850785648b7e058970cb2a.
PRODUCER_ID = 'watch-d1-v1'
IDENTITY = '5e138fabb1918e2fec8a570348c4a69070a4b00bdc2d488dea86d07ac45ad118'
CODE_SHA = 'eaac15eb9ad414b2bbdd63870b70bbc1a8b2d93d9f21f1308c43db3217c69bf1'
PROFILES_SHA = 'a2ba255264a5599ac717b2c3a464e34c7f1a32564e55ca6949937afa5aa1375e'
PROFILE = 'watches'
DESTINATION = 'watches-clank:proposal-review'
MAX_EXPORT = 32 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024
LABELS = ('unreviewed', 'editorial_keep', 'editorial_dismiss', 'editorial_research')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def dumps(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('Duplicate JSON key: ' + key)
            out[key] = value
        return out
    def constant(value):
        raise ValueError('Nonfinite JSON number: ' + value)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    # Overflow such as 1e999 is not passed to parse_constant.
    dumps(value)
    return value


@dataclass(frozen=True)
class Settings:
    producer_id: str
    producer: Path
    profiles: Path
    evidence_root: Path
    data_dir: Path
    enabled: bool = False

    def __post_init__(self):
        if self.producer_id != PRODUCER_ID:
            raise ValueError('Expected stable Watch producer_id: ' + PRODUCER_ID)
        if type(self.enabled) is not bool:
            raise ValueError('enabled must be boolean')
        for field in ('producer', 'profiles', 'evidence_root', 'data_dir'):
            value = Path(getattr(self, field))
            if not value.is_absolute():
                raise ValueError('Settings paths must be absolute: ' + field)
            object.__setattr__(self, field, value)
        if self.data_dir.resolve().is_relative_to(self.evidence_root.resolve()):
            raise ValueError('Consumer data must be outside producer evidence root')

    @property
    def generation(self):
        pointer = self.data_dir / 'CURRENT'
        if not pointer.exists():
            return None
        name = pointer.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'[a-f0-9]{32}', name):
            raise ValueError('Invalid sidecar generation')
        return self.data_dir / 'versions' / name

    @property
    def database(self):
        generation = self.generation
        return (generation if generation else self.data_dir / 'uninitialized') / 'proposals.sqlite3'


def authenticate(cfg):
    raw = cfg.producer.read_bytes()
    profiles = cfg.profiles.read_bytes()
    if digest(raw) != CODE_SHA or digest(profiles) != PROFILES_SHA:
        raise ValueError('Accepted D1 code/profile pin mismatch')
    module = types.ModuleType('_oem_pinned_d1')
    module.__file__ = str(cfg.producer)
    exec(compile(raw, str(cfg.producer), 'exec'), module.__dict__)
    # Reuse the exact delivered structural checks before its file I/O. This
    # allows explicit unavailable observations without weakening verified ones.
    tree = ast.parse(raw)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'validate')
    prefix = []
    for node in fn.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'raw' for t in node.targets):
            break
        prefix.append(node)
    fn.name = 'validate_structure'
    fn.body = prefix
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<pinned D1 structural validator>', 'exec'), module.__dict__)
    return module, strict_json(profiles)


def project_profiles(original):
    """Deterministic supported D1 config, with the authenticated watch object intact."""
    watch, = [p for p in original['profiles'] if p['id'] == PROFILE]
    if watch['destination'] != DESTINATION or len(watch['sources']) != 1 or watch['sources'][0]['url'] != 'https://monochrome-watches.com/feed/':
        raise ValueError('Unexpected authenticated watch profile')
    return {'schema_version': 'd1.profiles.v1', 'profiles': [copy.deepcopy(watch)]}


def checked_path(root, supplied):
    """Reject lexical escapes/reparse points BEFORE opening a supplied file."""
    root = Path(root)
    path = Path(supplied)
    if not path.is_absolute() or '..' in path.parts or any(':' in p for p in path.parts[1:]):
        raise ValueError('Unsafe evidence path')
    if not path.is_relative_to(root):
        raise ValueError('Evidence root escape')
    for component in reversed([path, *path.parents]):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Evidence links/junctions prohibited')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Resolved evidence escape')
    try:
        info = path.stat()
    except FileNotFoundError:
        return path
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
        raise ValueError('Unsuitable evidence file')
    return path


def read_bounded(path, limit):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Input byte limit exceeded')
    return raw


def routing_reason(payload):
    """Versioned conservative consumer boundary; never alters producer evidence."""
    path = Path(__file__).resolve().parents[1] / 'config' / 'discovery.routing.v1.json'
    routing = strict_json(read_bounded(path, 65536))
    if routing['schema_version'] != 'watch.discovery.routing.v1':
        raise ValueError('Unsupported consumer routing version')
    for pattern in routing['excluded_title_patterns']:
        if re.search(pattern, payload['source_title'], re.I):
            return 'watch.discovery.routing.v1: ' + pattern
    # Accessories are excluded as the primary subject, not for co-occurrence.
    # Ignore trailing attribute phrases (e.g. a watch "with a steel bracelet").
    # An accessory before a watch clue remains accessory-primary, including
    # "smart strap ... for your mechanical watch". All vocabulary is local config.
    scope = routing['accessory_primary_subject']
    subject = re.split(scope['attribute_boundary_pattern'], payload['source_title'],
                       maxsplit=1, flags=re.I)[0]
    accessory = re.search(scope['accessory_pattern'], subject, re.I)
    watch = re.search(scope['watch_pattern'], subject, re.I)
    if accessory and (watch is None or accessory.start() <= watch.start()):
        return 'watch.discovery.routing.v1: standalone accessory primary subject'
    return None


def prepare(cfg, export):
    module, profiles = authenticate(cfg)
    watch_profile, = [p for p in profiles['profiles'] if p['id'] == PROFILE]
    sources = {source['id']: source for source in watch_profile['sources']}
    if not cfg.evidence_root.is_dir():
        raise ValueError('Configured evidence root must be an existing directory')
    checked_path(cfg.evidence_root, str(cfg.evidence_root / '__root_probe__'))
    raw = read_bounded(export, MAX_EXPORT)
    lines = raw.decode('utf-8').splitlines()
    if len(lines) > 10000:
        raise ValueError('Record limit exceeded')
    prepared, seen, excluded, total_bytes = [], set(), {}, 0
    for line in lines:
        if not line.strip():
            continue
        if len(line.encode('utf8')) > 1024 * 1024:
            raise ValueError('Line limit exceeded')
        payload = strict_json(line)
        if not isinstance(payload, dict):
            raise ValueError('Proposal must be object')
        module.validate_structure(payload)
        profile_id = payload['category_profile']
        if profile_id not in (PROFILE, 'computing', 'tablets'):
            raise ValueError('Unsupported profile: ' + profile_id)
        if profile_id == PROFILE:
            if payload['suggested_consumer'] != DESTINATION:
                raise ValueError('Unexpected Watch destination')
            source = sources.get(payload['source_id'])
            if source is None or payload['publisher'] != source['publisher'] or payload['source_descriptor'] != source['description']:
                raise ValueError('Unqualified Watch source metadata')
        if payload['lead_id'] in seen:
            raise ValueError('Duplicate lead ID')
        seen.add(payload['lead_id'])
        e = payload['evidence']
        for key in ('text_sha256', 'capture_sha256'):
            if not isinstance(e[key], str) or not re.fullmatch('[a-f0-9]{64}', e[key]):
                raise ValueError('Invalid evidence SHA256')
        if e['offset'] + e['length'] > MAX_FILE or e['length'] != len(payload['supporting_passage'].encode('utf8')):
            raise ValueError('Invalid evidence bounds')
        if payload['subject_text'] != payload['source_title']:
            raise ValueError('Subject/headline mismatch')
        if not isinstance(payload['media_metadata'], list) or payload['content_source_field'] is not None and not isinstance(payload['content_source_field'], str):
            raise ValueError('Invalid source metadata')
        blobs, unavailable = {}, []
        # Both references are checked before either is read.
        paths = {kind: checked_path(cfg.evidence_root, e[kind + '_file']) for kind in ('text', 'capture')}
        for kind, path in paths.items():
            try:
                data = read_bounded(path, MAX_FILE)
            except FileNotFoundError:
                unavailable.append(kind + ': missing reference')
                continue
            total_bytes += len(data)
            if total_bytes > 128 * 1024 * 1024:
                raise ValueError('Export evidence byte budget exceeded')
            if digest(data) != e[kind + '_sha256']:
                raise ValueError('Evidence SHA256 mismatch')
            blobs[str(path)] = data
        if str(paths['text']) in blobs:
            text = blobs[str(paths['text'])]
            text.decode('utf8')
            if e['offset'] + e['length'] > len(text) or text[e['offset']:e['offset'] + e['length']].decode('utf8') != payload['supporting_passage']:
                raise ValueError('Supporting passage not literal evidence')
        if not unavailable:
            # The unchanged validator sees ONLY already bounded, root-checked
            # bytes, not a second opening of input-controlled host paths.
            class CapturedPath:
                def __init__(self, value, captured=blobs):
                    self.value = str(Path(value))
                    self.captured = captured
                def is_absolute(self):
                    return Path(self.value).is_absolute()
                def read_bytes(self):
                    return self.captured[self.value]
            module.Path = CapturedPath
            try:
                module.validate(payload)
            finally:
                module.Path = Path
        if payload['category_profile'] != PROFILE:
            reason = payload['category_profile'] + ': unsupported profile'
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        reason = routing_reason(payload)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        owned = {}
        for kind, path in paths.items():
            if str(path) in blobs:
                data = blobs[str(path)]
                owned[kind] = (digest(data), data)
        prepared.append((payload, owned, unavailable))
    return prepared, {'parsed': len(seen), 'accepted': len(prepared), 'excluded': excluded}


SCHEMA = '''
CREATE TABLE IF NOT EXISTS proposals (
 producer_id TEXT, lead_id TEXT, review TEXT NOT NULL, current_observation INTEGER,
 PRIMARY KEY(producer_id,lead_id));
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, producer_id TEXT, lead_id TEXT, fingerprint TEXT,
 payload TEXT, copied TEXT, evidence_status TEXT, limitations TEXT, imported_at TEXT);
CREATE TABLE IF NOT EXISTS review_history (
 id INTEGER PRIMARY KEY, producer_id TEXT, lead_id TEXT, label TEXT, reviewed_at TEXT);
'''


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def new_generation(cfg):
    directory = cfg.data_dir / 'versions' / uuid.uuid4().hex
    directory.mkdir(parents=True)
    database = directory / 'proposals.sqlite3'
    if cfg.database.exists():
        with closing(readonly(cfg.database)) as source, closing(sqlite3.connect(database)) as target:
            source.backup(target)
    conn = sqlite3.connect(database)
    conn.executescript(SCHEMA)
    return directory, conn


def publish(cfg, directory, conn):
    conn.commit()
    conn.close()
    # The consumer export is published WITH its matching sidecar by CURRENT.
    rows = read_database(directory / 'proposals.sqlite3')
    (directory / 'consumer.jsonl').write_text(''.join(dumps(row) + '\n' for row in rows), encoding='utf8')
    temp = cfg.data_dir / ('CURRENT.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(directory.name, encoding='ascii')
    os.replace(temp, cfg.data_dir / 'CURRENT')


def copy_blob(cfg, name, data):
    directory = cfg.data_dir / 'evidence'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if path.exists():
        if read_bounded(checked_path(directory, str(path)), MAX_FILE) != data:
            raise ValueError('Owned immutable evidence corruption')
    else:
        with path.open('xb') as stream:
            stream.write(data)
    return name


def import_export(cfg, export):
    if not cfg.enabled:
        raise ValueError('Discovery is disabled')
    prepared, result = prepare(cfg, export)  # NO consumer writes before full validation
    directory, conn = new_generation(cfg)
    result.update(inserted=0, changed=0, unchanged=0)
    try:
        with conn:
            for payload, blobs, unavailable in prepared:
                copied = {kind: copy_blob(cfg, name, data) for kind, (name, data) in blobs.items()}
                fingerprint = digest(dumps([payload, copied, unavailable]).encode())
                key = (cfg.producer_id, payload['lead_id'])
                previous = conn.execute('SELECT o.fingerprint FROM proposals p JOIN observations o ON o.id=p.current_observation WHERE p.producer_id=? AND p.lead_id=?', key).fetchone()
                if previous and previous[0] == fingerprint:
                    result['unchanged'] += 1
                    continue
                cursor = conn.execute('INSERT INTO observations(producer_id,lead_id,fingerprint,payload,copied,evidence_status,limitations,imported_at) VALUES(?,?,?,?,?,?,?,?)', (*key, fingerprint, dumps(payload), dumps(copied), 'unavailable/quarantined' if unavailable else 'verified', dumps(unavailable), datetime.now(UTC).isoformat()))
                conn.execute('INSERT INTO proposals VALUES(?,?,?,?) ON CONFLICT(producer_id,lead_id) DO UPDATE SET current_observation=excluded.current_observation', (*key, 'unreviewed', cursor.lastrowid))
                result['changed' if previous else 'inserted'] += 1
        publish(cfg, directory, conn)
    except BaseException:
        conn.close()
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return dict(result, status='IMPORTED', producer_id=cfg.producer_id, export=str(directory / 'consumer.jsonl'))


def read_database(database):
    if not Path(database).exists():
        return []
    with closing(readonly(database)) as conn:
        conn.row_factory = sqlite3.Row
        rows = []
        for row in conn.execute('SELECT p.review AS consumer_review,o.* FROM proposals p JOIN observations o ON o.id=p.current_observation ORDER BY o.producer_id,o.lead_id'):
            item = dict(row)
            for key in ('payload', 'copied', 'limitations'):
                item[key] = json.loads(item[key])
            item['history'] = [dict(x) for x in conn.execute('SELECT * FROM observations WHERE producer_id=? AND lead_id=? ORDER BY id', (item['producer_id'], item['lead_id']))]
            item['review_history'] = [dict(x) for x in conn.execute('SELECT label,reviewed_at FROM review_history WHERE producer_id=? AND lead_id=? ORDER BY id', (item['producer_id'], item['lead_id']))]
            rows.append(item)
        return rows


def read_inbox(cfg):
    return read_database(cfg.database)


def review(cfg, lead_id, label):
    if not cfg.enabled:
        raise ValueError('Discovery is disabled')
    if label not in LABELS:
        raise ValueError('Explicit editorial label required')
    if not any(x['producer_id'] == cfg.producer_id and x['lead_id'] == lead_id for x in read_inbox(cfg)):
        raise ValueError('Unknown producer/lead key')
    directory, conn = new_generation(cfg)
    try:
        with conn:
            conn.execute('UPDATE proposals SET review=? WHERE producer_id=? AND lead_id=?', (label, cfg.producer_id, lead_id))
            conn.execute('INSERT INTO review_history(producer_id,lead_id,label,reviewed_at) VALUES(?,?,?,?)', (cfg.producer_id, lead_id, label, datetime.now(UTC).isoformat()))
        publish(cfg, directory, conn)
    except BaseException:
        conn.close()
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return {'status': 'REVIEWED', 'producer_id': cfg.producer_id, 'lead_id': lead_id, 'consumer_review': label, 'product_admission': False}


def refresh(cfg):
    if not cfg.enabled:
        raise ValueError('Discovery is disabled')
    module, profiles = authenticate(cfg)
    projected = project_profiles(profiles)
    stage = cfg.data_dir / 'refresh-staging' / uuid.uuid4().hex
    stage.mkdir(parents=True)
    state = stage / 'state'
    state.mkdir()
    projection = stage / 'watch.profiles.json'
    projection.write_text(dumps(projected) + '\n', encoding='utf8')
    if strict_json(projection.read_bytes()) != project_profiles(profiles) or len(module.load_config(projection)) != 1:
        raise ValueError('Projection differs from authenticated watch object')
    # Accepted D1 has no limit flags: one source, one get(), at most three
    # redirect requests, one <=5MiB body and an 8s fetch-worker deadline.
    # These structural limits are below 6 requests / 10MiB / 90s.
    argv = [sys.executable, '-B', str(cfg.producer), 'run', '--config', str(projection), '--state', str(state)]
    execution = {'argv': argv, 'producer_identity': IDENTITY, 'code_sha256': CODE_SHA,
                 'original_profiles_sha256': PROFILES_SHA,
                 'projection_sha256': digest(projection.read_bytes()),
                 'budget': {'requests': 6, 'bytes': 10 * 1024 * 1024, 'seconds': 90, 'response_bytes': MAX_FILE}}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf8', timeout=90, shell=False)
        execution['exit_code'] = result.returncode
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        execution.update(exit_code=None, error='Producer timeout')
        stdout, stderr = exc.stdout or b'', exc.stderr or b''
        raise ValueError('Producer timeout; previous generation retained') from exc
    finally:
        for name, value in (('stdout.txt', locals().get('stdout', '')), ('stderr.txt', locals().get('stderr', ''))):
            (stage / name).write_bytes(value if isinstance(value, bytes) else value.encode('utf8'))
        (stage / 'execution.json').write_text(dumps(execution), encoding='utf8')
    try:
        report = strict_json(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValueError('Producer emitted invalid completion report') from exc
    (stage / 'report.json').write_text(dumps(report), encoding='utf8')
    if result.returncode != 0 or not isinstance(report, dict) or report.get('status') != 'COMPLETE':
        raise ValueError('Producer refresh failed or incomplete; previous generation retained')
    for key, maximum in (('network_requests', 6), ('network_bytes', 10 * 1024 * 1024)):
        if type(report.get(key)) is not int or not 0 <= report[key] <= maximum:
            raise ValueError('Producer accounting unavailable or over budget')
    _, checked = authenticate(cfg)
    if strict_json(projection.read_bytes()) != project_profiles(checked):
        raise ValueError('Projection changed during refresh')
    return import_export(replace(cfg, evidence_root=state), state / 'leads.jsonl')


def dashboard_handler(cfg, db_path):
    from .discovery_dashboard import dashboard_handler as factory
    return factory(cfg, db_path)


def evidence_bytes(cfg, name):
    from .discovery_dashboard import evidence_bytes as read
    return read(cfg, name)
