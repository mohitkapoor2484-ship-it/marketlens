"""Explicit offline Moomoo fixtures; none are actual quotes or forward evidence."""
import ast
import copy
import importlib.util
import socket
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marketlens.config import ROOT, Settings
from marketlens.api import create_app
from marketlens.db import Database, Dataset, IntradayAttempt, IntradayBar, IntradayConfigRevision, Signal
from marketlens.domain import digest
from marketlens.moomoo import (IntradayConfigRequest, MoomooBridge, capture, classify_time, intraday_config,
                               intraday_tick, intraday_view, normalise_bar, save_intraday_config)
from marketlens.service import Service
from marketlens.universe import UniverseConflict

NOW = datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc)


def raw(time='2026-10-30 15:50:00', code='US.AAPL', **extra):
    return {'code': code, 'time_key': time, 'open':100., 'high':102., 'low':99., 'close':101., 'volume':5000, **extra}


@pytest.fixture
def quote_service(tmp_path, monkeypatch):
    cfg = Settings(_env_file=None, data_mode='yahoo', database_url=f'sqlite:///{tmp_path.as_posix()}/quote.db',
                   scheduler_enabled=False, publish_enabled=False, report_dir=tmp_path / 'reports')
    svc = Service(cfg, Database(cfg.database_url))
    svc.initialise()
    monkeypatch.setattr('marketlens.moomoo.now_utc', lambda: NOW)
    svc.moomoo_provider = SimpleNamespace(fetch=lambda symbols: {'status':'success', 'symbols': {
        'US.' + s.removesuffix('.US'): {'rows':[raw(code='US.' + s.removesuffix('.US'))]} for s in symbols}})
    return svc


@pytest.mark.parametrize('time,session,day', [('2026-10-30T11:00:00+00:00','pre-market','2026-10-30'),
                                              ('2026-10-30T14:00:00+00:00','regular','2026-10-30'),
                                              ('2026-10-30T21:00:00+00:00','after-hours','2026-10-30'),
                                              ('2026-10-30T01:00:00+00:00','overnight','2026-10-30'),
                                              ('2026-11-02T01:30:00+00:00','overnight','2026-11-02'),
                                              ('2026-11-27T18:30:00+00:00','after-hours','2026-11-27')])
def test_session_labels_holidays_early_close_and_dst(time, session, day):
    assert classify_time(datetime.fromisoformat(time)) == (session, day)


@pytest.mark.parametrize('time', ['2026-10-31T12:00:00+00:00','2026-11-26T15:00:00+00:00','2026-10-31T01:00:00+00:00'])
def test_closed_and_holiday_sessions_rejected(time):
    with pytest.raises(ValueError, match='Outside'):
        classify_time(datetime.fromisoformat(time))


def test_completed_raw_bars_are_causal_and_exchange_time_is_preserved():
    value = normalise_bar(raw(), 'AAPL.US', NOW)
    assert value['start_at'] == '2026-10-30T19:50:00+00:00'
    assert value['end_at'] == '2026-10-30T19:55:00+00:00'
    assert value['currency'] == 'USD' and value['session'] == 'regular'
    assert normalise_bar(raw('2026-10-30 16:55:00'), 'AAPL.US', NOW) is None
    assert normalise_bar(raw('2026-10-30 16:50:00'), 'AAPL.US', NOW)


@pytest.mark.parametrize('changes', [{'code':'US.MSFT'},{'time_key':'2026-10-30 17:05:00'},
                                   {'time_key':'2026-10-30 15:51:00'}, {'close':float('nan')},
                                   {'volume':-1}, {'volume':True}, {'low':103.},
                                   {'time_key':'2026-11-01 01:30:00'}, {'time_key':'2026-03-08 02:30:00'}])
def test_bad_future_and_ambiguous_bars_rejected(changes):
    with pytest.raises((ValueError, TypeError)):
        normalise_bar(raw(**changes), 'AAPL.US', NOW)


def test_bars_persist_dedupe_and_vendor_revisions_append_without_forward_effects(quote_service):
    svc = quote_service
    before = svc.latest_scan()
    a = capture(svc, ['AAPL.US'], kind='probe')
    b = capture(svc, ['AAPL.US'], kind='probe')
    assert a['status'] == b['status'] == 'success'
    assert a['new_bars'] == 1 and b['new_bars'] == 0
    svc.moomoo_provider = SimpleNamespace(fetch=lambda symbols: {'status':'success', 'symbols':{'US.AAPL':{'rows':[raw(close=100.5)]}}})
    assert capture(svc, ['AAPL.US'])['new_bars'] == 1
    assert intraday_view(svc, 'AAPL.US')['bars'][0]['close'] == 100.5
    restarted = Service(svc.settings, Database(svc.settings.database_url))
    restarted.initialise()
    assert len(intraday_view(restarted, 'AAPL.US')['bars']) == 1
    assert intraday_config(restarted)['enabled'] is False
    with svc.db.session() as session:
        assert session.query(IntradayBar).count() == 2
        assert session.query(Signal).count() == session.query(Dataset).count() == 0
    assert svc.latest_scan() == before
    for table in ['intraday_bars','intraday_attempts','intraday_config_revisions']:
        with pytest.raises(Exception, match='immutable'):
            with svc.db.engine.begin() as conn:
                conn.execute(text('DELETE FROM ' + table))


def test_activation_requires_all_symbol_access_and_has_revision_conflicts(quote_service):
    svc = quote_service
    svc.moomoo_provider = SimpleNamespace(fetch=lambda symbols: {'status':'subscription_denied','message':'sensitive upstream diagnostic'})
    body = IntradayConfigRequest(enabled=True, symbols=['AAPL.US'], expected_revision=1)
    with pytest.raises(ValueError, match='not enabled'):
        save_intraday_config(svc, body)
    assert intraday_config(svc)['enabled'] is False
    assert 'sensitive' not in str(intraday_view(svc))
    svc.moomoo_provider = SimpleNamespace(fetch=lambda symbols: {'status':'success','symbols':{'US.AAPL':{'rows':[raw()]}}})
    with pytest.raises(ValueError, match='not enabled'):
        save_intraday_config(svc, body.model_copy(update={'symbols':['AAPL.US','MSFT.US']}))
    saved = save_intraday_config(svc, body)
    assert saved['enabled'] and saved['revision'] == 2
    with pytest.raises(UniverseConflict):
        save_intraday_config(svc, body)
    assert save_intraday_config(svc, body.model_copy(update={'enabled':False,'expected_revision':2}))['enabled'] is False


def test_failed_refresh_retains_cache_and_missing_rows_are_not_synthesized(quote_service):
    svc = quote_service
    svc.moomoo_provider = SimpleNamespace(fetch=lambda symbols: {'status':'success','symbols':{'US.AAPL':{'rows':[raw(),raw(),raw(high=1.)]}}})
    result = capture(svc, ['AAPL.US'])
    assert result['symbols'][0]['accepted'] == 1
    assert result['symbols'][0]['rejected']['Duplicate bar timestamp'] == 1
    svc.moomoo_provider = SimpleNamespace(fetch=lambda symbols: {'status':'gateway_unavailable'})
    capture(svc, ['AAPL.US'])
    view = intraday_view(svc, 'AAPL.US')
    assert len(view['bars']) == 1 and view['last_attempt']['status'] == 'gateway_unavailable'
    assert view['freshness'] == 'stale'


def test_scheduler_disabled_and_market_closed_do_not_request_quotes(quote_service, monkeypatch):
    svc = quote_service
    calls = []
    svc.moomoo_provider = SimpleNamespace(fetch=lambda *a: calls.append(a) or {})
    assert intraday_tick(svc)['status'] == 'disabled'
    with svc.db.session() as session:
        session.add(IntradayConfigRevision(created_at=NOW.isoformat(), payload={'enabled':True,'symbols':['AAPL.US']}, hash='fixture'))
    monkeypatch.setattr('marketlens.moomoo.now_utc', lambda: datetime(2026,10,31,12,tzinfo=timezone.utc))
    assert intraday_tick(svc)['status'] == 'market_closed' and calls == []


def test_api_origin_mode_and_market_guards(quote_service):
    svc = quote_service
    with TestClient(create_app(svc)) as client:
        assert client.get('/api/intraday').status_code == 200
        assert client.post('/api/intraday/probe', json={'symbol':'AAPL.US'}, headers={'Origin':'https://evil.test'}).status_code == 403
        assert client.post('/api/intraday/probe', json={'symbol':'BHP.AU'}).status_code == 422
        assert client.post('/api/intraday/probe', json={'symbol':'AAPL.US'}).json()['status'] == 'success'
        assert client.post('/api/intraday/refresh').status_code == 422
        assert client.put('/api/intraday/config', json={'enabled':True,'symbols':['AAPL.US'],'expected_revision':99}).status_code == 409
    svc.settings.data_mode = 'demo'
    with pytest.raises(ValueError, match='fixture'):
        capture(svc, ['AAPL.US'])


def test_unreachable_gateway_never_starts_sdk_or_substitutes_provider(quote_service, monkeypatch):
    monkeypatch.setattr('marketlens.moomoo.sdk_available', lambda: True)
    monkeypatch.setattr('marketlens.moomoo.socket.create_connection', lambda *a, **k: (_ for _ in ()).throw(OSError('closed')))
    monkeypatch.setattr('marketlens.moomoo.subprocess.run', lambda *a, **k: pytest.fail('No worker expected while gateway is down'))
    assert MoomooBridge(quote_service.settings).fetch(['AAPL.US'])['status'] == 'gateway_unavailable'
    with pytest.raises(ValueError):
        MoomooBridge(quote_service.settings).fetch(['AAPL;malicious'])


def test_worker_timeout_is_bounded_and_diagnostics_do_not_escape(quote_service, monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr('marketlens.moomoo.sdk_available', lambda: True)
    monkeypatch.setattr('marketlens.moomoo.socket.create_connection', lambda *a, **k: Connection())
    monkeypatch.setattr('marketlens.moomoo.subprocess.run', lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired('worker',35)))
    assert MoomooBridge(quote_service.settings).fetch(['AAPL.US'])['status'] == 'timeout'
    monkeypatch.setattr('marketlens.moomoo.subprocess.run', lambda *a, **k: SimpleNamespace(stdout='private account diagnostic',returncode=1))
    result = MoomooBridge(quote_service.settings).fetch(['AAPL.US'])
    assert result['status'] == 'sdk_error' and 'private' not in str(result)


def test_quote_worker_only_calls_approved_quote_methods_and_closes_context():
    path = ROOT / 'scripts/moomoo_quote_worker.py'
    tree = ast.parse(path.read_text())
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == 'context'}
    assert calls == {'get_global_state','subscribe','get_cur_kline','close'}
    spec = importlib.util.spec_from_file_location('quote_worker', path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    closed, requests = [], []
    class Quote:
        def get_global_state(self): return 0, {'qot_logined':True,'server_ver':'fixture','user_id':'PRIVATE'}
        def subscribe(self, codes, types, **kwargs): requests.append(kwargs); return 0, None
        def get_cur_kline(self, *a, **k): return 0, pd.DataFrame([raw()])
        def close(self): closed.append(True)
    sdk = SimpleNamespace(RET_OK=0, OpenQuoteContext=lambda **k: Quote(),
                          SysConfig=SimpleNamespace(set_all_thread_daemon=lambda *a:None,set_client_info=lambda *a:None),
                          SubType=SimpleNamespace(K_5M='5m'),Session=SimpleNamespace(ALL='ALL'),AuType=SimpleNamespace(NONE='NONE'))
    result = worker.collect({'codes':['US.AAPL'],'port':11111}, sdk)
    assert result['status'] == 'success' and closed == [True]
    assert requests[0]['session'] == 'ALL' and 'PRIVATE' not in str(result)
