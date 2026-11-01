"""All observations in these tests are explicit local stubs, never uploaded."""
import base64
import copy
import hashlib
import json
from types import SimpleNamespace
from threading import RLock

import httpx
import pytest
from pydantic import SecretStr

from marketlens.domain import canonical, stamp
from marketlens.publication import SCHEMA, GitHubDataStore, build_snapshot, publication_status, publish_research, upload_tick
from marketlens.providers import ProviderError


@pytest.fixture
def public_stub(service, tmp_path):
    scan = copy.deepcopy(service.latest_scan())
    scan['mode'] = 'yahoo'
    for c in scan['candidates']:
        c['mode'] = 'yahoo'
    for c in scan['shortlist']:
        c['mode'] = 'yahoo'
    health = copy.deepcopy(service.data_health())
    health['mode'] = 'yahoo'
    def stock(symbol):
        value = copy.deepcopy(service.stock(symbol))
        value['mode'] = 'yahoo'
        value['credentials'] = 'DO-NOT-PUBLISH'
        value['bars'][0]['account_balance'] = 'DO-NOT-PUBLISH'
        return value
    cfg = service.settings.model_copy(update={'data_mode': 'yahoo', 'publish_enabled': True, 'publish_state_path': tmp_path / 'state.json', 'publish_github_token': SecretStr('DO-NOT-PUBLISH')})
    return SimpleNamespace(settings=cfg, db=service.db, universe=service.universe, operations=RLock(),
                           latest_scan=lambda: scan, data_health=lambda: health, stock=stock,
                           job=lambda key, fn: fn())


def test_public_export_excludes_accounts_secrets_jobs_and_saved_results(public_stub):
    public_stub.latest_scan()['rules']['portfolio']['starting_balance'] = 987654321
    public_stub.latest_scan()['credentials'] = 'DO-NOT-PUBLISH'
    public_stub.data_health()['jobs'] = [{'detail': 'DO-NOT-PUBLISH'}]
    public_stub.data_health()['symbols'][0]['credentials'] = 'DO-NOT-PUBLISH'
    result = build_snapshot(public_stub)
    encoded = canonical(result)
    assert 'DO-NOT-PUBLISH' not in encoded
    assert '987654321' not in encoded
    assert result['overview']['paper'] == {'private': True, 'kind': 'local_only'}
    assert 'portfolio' not in result['settings']['rules']
    assert 'costs' not in result['settings']['rules']
    assert result['backtests'] == result['reports'] == []
    assert result['intraday']['local_only'] is True
    assert 'bars' not in result['intraday'] and 'gateway' not in result['intraday'] and 'config' not in result['intraday']
    assert len(result['stocks']) == 14
    assert all(len(s['bars']) <= 300 for s in result['stocks'].values())
    assert result['overview']['scan']['mode'] == 'yahoo'


def test_demo_export_cannot_impersonate_actual_data(service):
    with pytest.raises(ValueError, match='fixture'):
        build_snapshot(service)


def test_discovery_publication_keeps_only_research_allowlist(public_stub):
    from marketlens.db import DiscoveryRun
    public_stub.universe_revision = public_stub.latest_scan()['universe_revision']
    payload = {'rows': [{'symbol':'TEST.AU', 'source_at':stamp(), 'market':'ASX',
                         'credentials':'DO-NOT-PUBLISH', 'instrument':{'membership_evidence':'PRIVATE'},
                         'account_id':'DO-NOT-PUBLISH'}], 'errors':[], 'groups':[]}
    with public_stub.db.session() as session:
        session.add(DiscoveryRun(created_at=stamp(), mode='yahoo', market='ASX', request_hash='explicit-test', status='success', payload=payload))
    exported = build_snapshot(public_stub)
    assert exported['discovery']['markets'][0]['rows'][0]['symbol'] == 'TEST.AU'
    assert 'DO-NOT-PUBLISH' not in canonical(exported)
    assert 'membership_evidence' not in canonical(exported['discovery'])
    assert 'universe_revision' not in exported['discovery']


def test_mixed_data_mode_is_rejected(public_stub):
    public_stub.latest_scan()['candidates'][0]['mode'] = 'demo'
    with pytest.raises(ValueError, match='Mixed'):
        build_snapshot(public_stub)


def test_no_scan_cannot_be_uploaded(public_stub):
    public_stub.latest_scan = lambda: {'mode': 'yahoo'}
    with pytest.raises(ValueError, match='scan'):
        build_snapshot(public_stub)


class FakeGitHub:
    def __init__(self):
        self.blobs, self.trees, self.commits, self.calls = {}, {}, {}, []
        self.head = None
        self.reject_patch = False

    def transport(self, request):
        path = request.url.path.split('/marketlens/')[-1]
        if request.url.path.rstrip('/').endswith('/marketlens'):
            path = ''
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path == '':
            return httpx.Response(200, json={'full_name': 'mohitkapoor2484-ship-it/marketlens', 'private': False, 'default_branch': 'main'})
        if path.startswith('git/ref/heads/'):
            return httpx.Response(200, json={'object': {'sha': self.head}}) if self.head else httpx.Response(404)
        if path.startswith('contents/latest.json'):
            nodes = self.trees[self.commits[self.head]['tree']]
            blob = next(x['sha'] for x in nodes if x['path'] == 'latest.json')
            return httpx.Response(200, json={'content': self.blobs[blob]['content']})
        if path == 'git/blobs':
            sha = hashlib.sha1(request.content).hexdigest()
            self.blobs[sha] = body
        elif path == 'git/trees':
            sha = hashlib.sha1(request.content).hexdigest()
            self.trees[sha] = body['tree']
        elif path == 'git/commits':
            sha = hashlib.sha1(request.content).hexdigest()
            self.commits[sha] = body
        elif path == 'git/refs' or path.startswith('git/refs/heads/'):
            if self.reject_patch:
                return httpx.Response(422)
            if request.method == 'PATCH':
                assert body['force'] is False
            self.head = body['sha']
            return httpx.Response(200, json={'object': {'sha': self.head}})
        else:
            raise AssertionError((request.method, path))
        return httpx.Response(201, json={'sha': sha})


@pytest.fixture
def fake_store(public_stub, monkeypatch):
    remote = FakeGitHub()
    real_client = httpx.Client
    monkeypatch.setattr('marketlens.publication.httpx.Client', lambda **kw: real_client(**kw, transport=httpx.MockTransport(remote.transport)))
    return remote, lambda cfg: GitHubDataStore(cfg)


def test_atomic_manifest_references_immutable_data_and_restart_reuses_it(public_stub, fake_store):
    remote, factory = fake_store
    first = publish_research(public_stub, factory)
    assert first['status'] == 'success'
    assert first['manifest_commit'] == remote.head
    manifest_commit = remote.commits[remote.head]
    assert manifest_commit['parents'] == [first['data_commit']]
    nodes = remote.trees[remote.commits[first['data_commit']]['tree']]
    data_blob = next(x['sha'] for x in nodes if x['path'] == 'data.json')
    data = base64.b64decode(remote.blobs[data_blob]['content'])
    assert hashlib.sha256(data).hexdigest() == first['content_hash']
    assert b'DO-NOT-PUBLISH' not in data
    # Simulate missing local state after restart; remote manifest is authoritative.
    public_stub.settings.publish_state_path.unlink()
    before = len(remote.blobs)
    second = publish_research(public_stub, factory)
    assert second['data_commit'] == first['data_commit']
    assert len(remote.blobs) == before + 1  # heartbeat only, not another full dataset
    assert all('main' not in path for _, path, _ in remote.calls)


def test_failed_upload_preserves_last_published_state_and_sanitizes_errors(public_stub, fake_store):
    remote, factory = fake_store
    first = publish_research(public_stub, factory)
    remote.reject_patch = True
    with pytest.raises(ProviderError, match='422'):
        publish_research(public_stub, factory)
    current = publication_status(public_stub)
    assert current['status'] == 'failed'
    assert current['published_at'] == first['published_at']
    assert current['manifest_commit'] == remote.head == first['manifest_commit']
    assert 'DO-NOT-PUBLISH' not in canonical(current)


def test_disabled_publisher_never_looks_up_credentials(service):
    def forbidden(*args):
        raise AssertionError('Must not connect')
    assert publish_research(service, forbidden)['status'] == 'disabled'


@pytest.mark.parametrize('branch', ['main', 'master', 'gh-pages'])
def test_code_branches_cannot_be_modified(public_stub, branch):
    public_stub.settings.publish_branch = branch
    with pytest.raises(ValueError, match='dedicated'):
        GitHubDataStore(public_stub.settings)


def test_oversized_export_never_connects_and_does_not_expose_exception(public_stub, monkeypatch):
    monkeypatch.setattr('marketlens.publication.build_snapshot', lambda svc: {'blob': 'x' * 8_000_001})
    with pytest.raises(ProviderError, match='8 MB'):
        publish_research(public_stub, lambda cfg: pytest.fail('Must not upload'))
    assert publication_status(public_stub)['status'] == 'failed'


def test_busy_research_skips_scheduled_upload_without_overlapping(public_stub):
    from threading import Lock
    public_stub.operations = Lock()
    public_stub.operations.acquire()
    try:
        assert upload_tick(public_stub)['status'] == 'busy'
    finally:
        public_stub.operations.release()


def test_five_minute_publisher_runs_independently_of_market_scheduler(public_stub, monkeypatch):
    from marketlens.jobs import start_scheduler
    entries = []
    class Scheduler:
        def __init__(self, **kwargs):
            pass
        def add_job(self, *args, **kwargs):
            entries.append((args, kwargs))
        def start(self):
            pass
    public_stub.settings.scheduler_enabled = False
    monkeypatch.setattr('marketlens.jobs.BackgroundScheduler', Scheduler)
    start_scheduler(public_stub)
    by_id = {entry[1]['id']:entry[1] for entry in entries}
    assert set(by_id) == {'moomoo-us-quotes','research-snapshot-upload'}
    assert by_id['research-snapshot-upload']['minutes'] == 5
    assert by_id['moomoo-us-quotes']['minutes'] == 5


def test_local_manual_refresh_route_runs_cycle_and_keeps_origin_guard(service, monkeypatch):
    from fastapi.testclient import TestClient
    from marketlens.api import create_app
    calls = []
    monkeypatch.setattr('marketlens.api.cycle', lambda svc: calls.append('cycle') or {'publication': {'status': 'success'}})
    with TestClient(create_app(service)) as client:
        assert client.post('/api/publication/refresh', headers={'Origin': 'https://hostile.example'}).status_code == 403
        assert calls == []
        response = client.post('/api/publication/refresh')
        assert response.status_code == 200
        assert calls == ['cycle']
        assert client.get('/api/publication').json()['enabled'] is False
