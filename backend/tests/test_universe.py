import copy
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marketlens.api import create_app
from marketlens.config import Settings
from marketlens.db import Database, PaperAccount, Scan, UniverseRevision
from marketlens.providers import DemoProvider
from marketlens.service import Service
from marketlens.universe import UniverseConflict, save_universe, universe_view, validate_universe


BASE = [
    {'symbol': 'BHP.AU', 'name': 'BHP', 'market': 'ASX', 'benchmark': 'STW.AU', 'role': 'stock'},
    {'symbol': 'STW.AU', 'name': 'ASX benchmark', 'market': 'ASX', 'benchmark': 'STW.AU', 'role': 'benchmark'},
    {'symbol': 'SPY.US', 'name': 'US benchmark', 'market': 'US', 'benchmark': 'SPY.US', 'role': 'benchmark'},
]
ETF = {'symbol': 'VAS.AU', 'name': 'ETF test instrument', 'market': 'ASX', 'benchmark': 'STW.AU', 'role': 'etf'}


@pytest.fixture
def universe_service(tmp_path):
    path = tmp_path / 'defaults.json'
    path.write_text(json.dumps(BASE), encoding='utf-8')
    config = Settings(_env_file=None, data_mode='yahoo', database_url=f'sqlite:///{tmp_path.as_posix()}/universe.db', universe_path=path, report_dir=tmp_path / 'reports')
    service = Service(config, Database(config.database_url))
    service.initialise()  # Actual source mode does not download or fabricate prices.
    return service


def test_save_is_immediate_persistent_and_does_not_modify_default_file(universe_service):
    svc = universe_service
    original = svc.settings.universe_path.read_bytes()
    saved = save_universe(svc, BASE + [ETF], 1)
    assert saved['revision'] == 2
    assert svc.instrument('VAS.AU').role == 'etf'
    assert svc.latest_scan()['universe_revision'] == 2
    assert any(x['symbol'] == 'VAS.AU' for x in svc.latest_scan()['unavailable'])
    restarted = Service(svc.settings, Database(svc.settings.database_url))
    restarted.initialise()
    assert restarted.universe_revision == 2
    assert restarted.instrument('VAS.AU').role == 'etf'
    assert svc.settings.universe_path.read_bytes() == original


def test_duplicate_and_cross_market_benchmark_rejected(universe_service):
    with pytest.raises(ValueError, match='Duplicate'):
        save_universe(universe_service, BASE + [BASE[0]], 1)
    wrong = copy.deepcopy(BASE)
    wrong[0]['benchmark'] = 'SPY.US'
    with pytest.raises(ValueError, match='same market'):
        save_universe(universe_service, wrong, 1)
    assert universe_service.universe_revision == 1


def test_required_benchmark_and_size_limits():
    broken = copy.deepcopy(BASE)
    broken[1]['role'] = 'etf'
    with pytest.raises(ValueError, match='remain a benchmark'):
        validate_universe(broken)
    many = BASE + [{'symbol': f'T{i}.US', 'name': 'Test', 'market': 'US', 'benchmark': 'SPY.US'} for i in range(500)]
    with pytest.raises(ValueError, match='500'):
        validate_universe(many)


def test_revision_conflicts_and_no_op_saves(universe_service):
    svc = universe_service
    assert save_universe(svc, BASE, 1)['revision'] == 1
    save_universe(svc, BASE + [ETF], 1)
    with pytest.raises(UniverseConflict, match='another session'):
        save_universe(svc, BASE, 1)
    assert svc.universe_revision == 2


@pytest.mark.parametrize('obligation', ['position', 'pending', 'receivable'])
def test_active_paper_instruments_cannot_be_removed(universe_service, obligation):
    svc = universe_service
    save_universe(svc, BASE + [ETF], 1)
    with svc.db.session() as session:
        account = session.get(PaperAccount, 'forward')
        state = copy.deepcopy(account.state)
        if obligation == 'position':
            state['positions']['test'] = {'symbol': 'BHP.AU'}
        elif obligation == 'pending':
            state['orders'].append({'symbol': 'BHP.AU', 'status': 'pending'})
        else:
            state['receivables'].append({'symbol': 'BHP.AU'})
        account.state = state
    assert 'BHP.AU' in universe_view(svc)['protected']
    with pytest.raises(ValueError, match='active paper'):
        save_universe(svc, [item for item in BASE + [ETF] if item['symbol'] != 'BHP.AU'], 2)
    assert svc.instrument('BHP.AU')


def test_universe_revisions_are_append_only(universe_service):
    with pytest.raises(Exception, match='immutable audit record'):
        with universe_service.db.engine.begin() as connection:
            connection.execute(text('UPDATE universe_revisions SET hash=\'changed\''))
    with pytest.raises(Exception, match='immutable audit record'):
        with universe_service.db.engine.begin() as connection:
            connection.execute(text('DELETE FROM universe_revisions'))


def test_migration_upgrades_existing_version_one_database(universe_service):
    svc = universe_service
    with svc.db.engine.begin() as connection:
        connection.execute(text('DROP TABLE universe_revisions'))
        connection.execute(text('DROP TABLE discovery_runs'))
        for table in ('intraday_bars', 'intraday_attempts', 'intraday_config_revisions'):
            connection.execute(text('DROP TABLE ' + table))
        connection.execute(text('DELETE FROM schema_migrations WHERE version>=2'))
    svc.initialise()
    with svc.db.session() as session:
        assert session.query(UniverseRevision).count() == 1
        assert session.execute(text('SELECT version FROM schema_migrations ORDER BY version')).scalars().all() == [1, 2, 3, 4]
        assert session.execute(text('SELECT COUNT(*) FROM discovery_runs')).scalar() == 0


def test_etfs_are_included_in_cached_scans_and_edits_create_no_forward_signals(universe_service):
    svc = universe_service
    svc.settings.data_mode = 'demo'
    svc.provider = DemoProvider()
    save_universe(svc, BASE + [ETF], 1)
    svc.refresh()
    scan = svc.scan(record_forward=False)
    assert any(c['symbol'] == 'VAS.AU' for c in scan['candidates'])
    from marketlens.db import Signal
    with svc.db.session() as session:
        assert session.query(Signal).count() == 0


def test_saved_backtest_reruns_after_symbol_removed(universe_service):
    svc = universe_service
    svc.settings.data_mode = 'demo'
    svc.provider = DemoProvider()
    save_universe(svc, BASE + [ETF], 1)
    svc.refresh()
    request = {'symbol': 'BHP.AU', 'start': '2020-01-02', 'end': '2022-12-30', 'parameters': {'strategy': 'breakout', 'lookback': 20, 'stop_atr': 2, 'target_r': 2}, 'robustness': False}
    run = svc.backtest(request)
    old_scan = svc.latest_scan()
    save_universe(svc, [item for item in BASE + [ETF] if item['symbol'] != 'BHP.AU'], 2)
    rerun = svc.backtest({**request, 'rerun_id': run['id']})
    assert rerun['rerun_matches'] is True
    with svc.db.session() as session:
        assert session.get(Scan, old_scan['scan_id']).payload['universe_revision'] == 2


def test_local_api_edit_validation_conflicts_and_origin_guard(universe_service):
    with TestClient(create_app(universe_service)) as client:
        current = client.get('/api/universe').json()
        assert current['revision'] == 1
        body = {'items': BASE + [ETF], 'expected_revision': 1}
        assert client.put('/api/universe', json=body, headers={'Origin': 'https://hostile.example'}).status_code == 403
        assert client.put('/api/universe', json=body).status_code == 200
        assert client.put('/api/universe', json=body).status_code == 409
        invalid = {**body, 'items': BASE + [BASE[0]], 'expected_revision': 2}
        assert client.put('/api/universe', json=invalid).status_code == 422
        assert any(i['symbol'] == 'VAS.AU' for i in client.get('/api/settings').json()['universe'])
        assert client.get('/api/stocks/VAS.AU').status_code == 422
