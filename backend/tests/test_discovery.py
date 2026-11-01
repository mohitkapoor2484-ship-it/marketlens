"""Explicit mocked observations only. Never publish these test fixtures."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marketlens.api import create_app
from marketlens.config import ROOT, Settings
from marketlens.db import Database, Dataset, DiscoveryRun, Signal
from marketlens.discovery import (DiscoveryRequest, ImportRequest, YahooDiscovery, discovery_view,
                                  import_candidates, normalize, run_discovery)
from marketlens.domain import now_utc, stamp
from marketlens.providers import DemoProvider, ProviderError
from marketlens.service import Service
from marketlens.universe import UniverseConflict


@pytest.fixture
def discovery_service(tmp_path, monkeypatch):
    cfg = Settings(_env_file=None, data_mode='yahoo', database_url=f'sqlite:///{tmp_path.as_posix()}/discover.db',
                   report_dir=tmp_path / 'reports', universe_path=ROOT / 'universe.json', scheduler_enabled=False, publish_enabled=False)
    svc = Service(cfg, Database(cfg.database_url))
    svc.initialise()
    monkeypatch.setattr('marketlens.discovery.source_status', lambda r, lag=180: 'current')
    return svc


def quote(market='ASX', kind='stock', ticker=None, **extra):
    return {'symbol': ticker or ('NEW.AX' if market == 'ASX' else 'TSLA'), 'longName': 'Explicit test fixture',
            'exchange': 'ASX' if market == 'ASX' else 'NMS', 'currency': 'AUD' if market == 'ASX' else 'USD',
            'quoteType': 'EQUITY' if kind == 'stock' else 'ETF', 'regularMarketPrice': 25.,
            'averageDailyVolume3Month': 1_000_000, 'regularMarketVolume': 2_000_000,
            'regularMarketTime': (now_utc() - timedelta(days=1)).timestamp(), 'regularMarketChangePercent': -1.2,
            'fiftyDayAverage': 20., 'twoHundredDayAverage': 18., **extra}


def adapter(svc, market='ASX', extra=()):
    calls = []
    def screen(request, kind):
        calls.append((request, kind))
        return {'total': 400, 'quotes': [quote(market, kind, 'NEW.AX' if kind == 'stock' else 'ETF.AX') if market == 'ASX' else quote('US', kind, 'TSLA' if kind == 'stock' else 'QQQ'), *extra]}
    svc.discovery_provider = SimpleNamespace(screen=screen)
    return calls


def test_preset_and_explicit_frontend_numbers_share_the_same_cache_key(discovery_service):
    svc = discovery_service
    calls = adapter(svc)
    first = run_discovery(svc, DiscoveryRequest())
    again = run_discovery(svc, DiscoveryRequest(min_price=.3, min_average_volume=100000, min_turnover=1000000))
    assert again['cached'] and again['run_id'] == first['run_id'] and len(calls) == 2


@pytest.mark.parametrize('market,kind,ticker,symbol', [('ASX','stock','NEW.AX','NEW.AU'),('ASX','etf','ETF.AX','ETF.AU'),('US','stock','BRK-B','BRK.B.US'),('US','etf','QQQ','QQQ.US')])
def test_exchange_mapping_and_explicit_statistics(market, kind, ticker, symbol):
    row = normalize(quote(market, kind, ticker), DiscoveryRequest(market=market).effective(), kind, now_utc())
    assert row['symbol'] == symbol
    assert row['relative_volume_3m'] == 2
    assert row['approximate_turnover'] == 25_000_000
    assert row['change_percent'] == -1.2
    assert row['instrument']['role'] == kind
    assert 'score' not in row and 'plan' not in row and 'qualified' not in row


@pytest.mark.parametrize('override', [{'currency':'USD'},{'exchange':'PNK'},{'quoteType':'OPTION'},{'symbol':'WRONG'},
                                      {'regularMarketPrice':float('nan')},{'averageDailyVolume3Month':0},
                                      {'regularMarketVolume':-1},{'regularMarketTime':None},
                                      {'regularMarketTime':(now_utc()+timedelta(days=1)).timestamp()},
                                      {'regularMarketPrice':.01}])
def test_bad_or_filtered_rows_are_excluded(override):
    with pytest.raises((ValueError, TypeError)):
        normalize(quote(**override), DiscoveryRequest().effective(), 'stock', now_utc())


def test_persistence_cache_counts_and_no_paper_side_effects(discovery_service):
    svc = discovery_service
    calls = adapter(svc, extra=[quote(), quote(currency='USD')])
    found = run_discovery(svc, DiscoveryRequest(kind='stock'))
    assert len(found['rows']) == 1 and found['groups'][0]['accepted'] == 1
    assert found['groups'][0]['skipped'] == {'Duplicate symbol':1, 'Wrong exchange or currency':1}
    assert found['groups'][0]['capped']
    restarted = Service(svc.settings, Database(svc.settings.database_url))
    restarted.initialise()
    restarted.discovery_provider = SimpleNamespace(screen=lambda *a: pytest.fail('Should reuse the persisted cache'))
    again = run_discovery(restarted, DiscoveryRequest(kind='stock'))
    assert again['cached'] and again['run_id'] == found['run_id'] and len(calls) == 1
    assert len(svc.universe) == 14
    with svc.db.session() as s:
        assert s.query(Signal).count() == s.query(Dataset).count() == 0
        with pytest.raises(Exception, match='immutable'):
            s.execute(text('UPDATE discovery_runs SET status=\'edited\''))


def test_empty_screen_never_forces_candidates(discovery_service):
    svc = discovery_service
    svc.discovery_provider = SimpleNamespace(screen=lambda *a: {'total': 0, 'quotes': []})
    result = run_discovery(svc, DiscoveryRequest())
    assert result['rows'] == [] and result['status'] == 'success'


def test_failure_retains_successful_snapshot_and_partial_failure_visible(discovery_service):
    svc = discovery_service
    adapter(svc)
    good = run_discovery(svc, DiscoveryRequest(kind='stock'))
    svc.discovery_provider = SimpleNamespace(screen=lambda *a: (_ for _ in ()).throw(ProviderError('secret URL must not escape')))
    with pytest.raises(ProviderError):
        run_discovery(svc, DiscoveryRequest(kind='etf'))
    shown = discovery_view(svc)['markets'][0]
    assert shown['run_id'] == good['run_id'] and len(shown['rows']) == 1
    assert shown['status'] == 'failed' and 'secret' not in str(shown)
    svc.discovery_provider = SimpleNamespace(screen=lambda req, kind: {'quotes': [quote()], 'total':1} if kind == 'stock' else (_ for _ in ()).throw(ProviderError('fail')))
    assert run_discovery(svc, DiscoveryRequest())['status'] == 'partial'


def test_import_validates_recorded_membership_staleness_and_revision(discovery_service, monkeypatch):
    svc = discovery_service
    adapter(svc)
    run = run_discovery(svc, DiscoveryRequest(kind='stock'))
    with pytest.raises(ValueError, match='not part'):
        import_candidates(svc, ImportRequest(run_id=run['run_id'], symbols=['FAKE.US'], expected_revision=1))
    body = ImportRequest(run_id=run['run_id'], symbols=['NEW.AU'], expected_revision=1)
    with pytest.raises(UniverseConflict):
        import_candidates(svc, body.model_copy(update={'expected_revision':10}))
    monkeypatch.setattr('marketlens.discovery.source_status', lambda *a: 'stale')
    with pytest.raises(ValueError, match='stale'):
        import_candidates(svc, body)
    assert len(svc.universe) == 14


def test_import_downloads_only_selected_and_missing_dependencies_without_signals(discovery_service):
    svc = discovery_service
    adapter(svc)
    run = run_discovery(svc, DiscoveryRequest(kind='stock'))
    demo = DemoProvider()
    fetched = []
    svc.provider = SimpleNamespace(fetch=lambda inst, start, end: fetched.append(inst.symbol) or demo.fetch(inst, start, '2025-12-31'))
    response = import_candidates(svc, ImportRequest(run_id=run['run_id'], symbols=['NEW.AU','NEW.AU'], expected_revision=1))
    assert response['added'] == ['NEW.AU'] and set(fetched) == {'NEW.AU','STW.AU'}
    assert response['failed'] == [] and svc.universe_revision == 2
    assert svc.instrument('NEW.AU').membership_evidence.startswith('discovery:')
    with svc.db.session() as session:
        assert session.query(Signal).count() == 0
    duplicate = import_candidates(svc, ImportRequest(run_id=run['run_id'], symbols=['NEW.AU'], expected_revision=2))
    assert duplicate['added'] == [] and len(fetched) == 2


def test_failed_import_is_explicit_and_does_not_fabricate_data(discovery_service):
    svc = discovery_service
    adapter(svc)
    run = run_discovery(svc, DiscoveryRequest(kind='stock'))
    svc.provider = SimpleNamespace(fetch=lambda *a: (_ for _ in ()).throw(ProviderError('unavailable')))
    result = import_candidates(svc, ImportRequest(run_id=run['run_id'], symbols=['NEW.AU'], expected_revision=1))
    assert 'NEW.AU' in result['failed'] and svc.instrument('NEW.AU')
    with svc.db.session() as session:
        assert session.query(Dataset).count() == session.query(Signal).count() == 0


def test_api_origin_validation_and_modes(discovery_service):
    svc = discovery_service
    adapter(svc)
    with TestClient(create_app(svc)) as client:
        assert client.get('/api/discovery').status_code == 200
        assert client.post('/api/discovery', json={'market':'ASX'}, headers={'Origin':'https://evil.test'}).status_code == 403
        assert client.post('/api/discovery', json={'limit':251}).status_code == 422
        assert client.post('/api/discovery', json={'market':'ASX'}).status_code == 200
        assert client.post('/api/discovery/import', json={'run_id':1,'symbols':['NEW.AU'],'expected_revision':100}).status_code == 409
    svc.settings.data_mode = 'demo'
    with pytest.raises(ValueError, match='requires'):
        run_discovery(svc, DiscoveryRequest())


def test_quote_freshness_uses_exchange_date_not_retrieval_age(monkeypatch):
    from marketlens.discovery import source_status
    monkeypatch.setattr('marketlens.discovery.latest_closed', lambda *a, **kw: '2026-10-30')
    assert source_status({'market':'US','source_at':'2026-10-30T20:00:00+00:00'}) == 'current'
    assert source_status({'market':'ASX','source_at':'2026-10-29T23:30:00+00:00'}) == 'current'
    assert source_status({'market':'US','source_at':'2026-10-29T20:00:00+00:00'}) == 'stale'
    assert source_status({'market':'ASX','source_at':'2026-11-01T23:30:00+00:00'}) == 'session not yet eligible'


def test_saved_discovery_read_remains_available_during_source_downloads(discovery_service):
    svc = discovery_service
    adapter(svc)
    run_discovery(svc, DiscoveryRequest(kind='stock'))
    with TestClient(create_app(svc)) as client, ThreadPoolExecutor(max_workers=1) as executor:
        with svc.operations:
            response = executor.submit(client.get, '/api/discovery').result(timeout=3)
            assert response.status_code == 200
            assert len(response.json()['markets'][0]['rows']) == 1
