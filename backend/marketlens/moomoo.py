"""Optional US quote research; strictly separate from daily data and execution."""
import importlib.util
import json
import math
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import Field

from .calendars import calendar, session_times
from .config import ROOT
from .db import IntradayAttempt, IntradayBar, IntradayConfigRevision
from .domain import Bar, StrictModel, canonical, digest, now_utc, parse_time, stamp
from .universe import UniverseConflict

VERSION = 'moomoo-quotes-1'
NY = ZoneInfo('America/New_York')
INFO = {'name': 'Moomoo OpenD · US 5-minute research', 'interval': '5m',
        'sessions': 'Requests regular, pre-market, after-hours and overnight where supported and entitled',
        'coverage': 'Selected US stocks/ETFs only. Returned session coverage and volume may differ from consolidated US data.',
        'licence': 'Account-specific API permissions and quotas. Promotional free access is not guaranteed. Local research only; redistribution permission unresolved.',
        'documentation': 'https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html',
        'publication': 'Local only; no Moomoo bars or account diagnostics are published',
        'notice': 'Intraday monitoring only. Daily scanner, backtests and forward paper signals use their existing EOD datasets.'}


class IntradayConfigRequest(StrictModel):
    enabled: bool = False
    symbols: list[str] = Field(min_length=1, max_length=20)
    expected_revision: int = Field(ge=1)


class ProbeRequest(StrictModel):
    symbol: str = Field('AAPL.US', pattern=r'^[A-Z0-9][A-Z0-9.\-]{0,19}\.US$')


def sdk_available():
    return importlib.util.find_spec('moomoo') is not None


class MoomooBridge:
    def __init__(self, settings):
        self.settings = settings

    def fetch(self, symbols):
        if not 1 <= len(symbols) <= 20 or any(not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,19}\.US', s) for s in symbols):
            raise ValueError('Use 1–20 canonical US symbols')
        if not sdk_available():
            return {'status': 'sdk_missing', 'message': 'Install backend/moomoo-requirements.txt, then restart MarketLens'}
        try:
            with socket.create_connection(('127.0.0.1', self.settings.moomoo_opend_port), timeout=1):
                pass
        except OSError:
            return {'status': 'gateway_unavailable', 'message': 'Moomoo OpenD is not reachable at the configured local port. Open it and sign in locally'}
        request = {'codes': ['US.' + s.removesuffix('.US') for s in symbols], 'port': self.settings.moomoo_opend_port}
        try:
            completed = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/moomoo_quote_worker.py')],
                                       input=canonical(request), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=35,
                                       cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        except subprocess.TimeoutExpired:
            return {'status': 'timeout', 'message': 'OpenD quote request timed out; no synthetic replacement used'}
        except OSError:
            return {'status': 'sdk_error', 'message': 'Could not start the local quote worker'}
        # SDK output can contain diagnostics. Never expose it through the API/logs.
        if len(completed.stdout) > 4_000_000:
            return {'status': 'invalid_response', 'message': 'Quote response exceeded the allowed size'}
        lines = [x for x in completed.stdout.splitlines() if x.startswith('MARKETLENS_QUOTE_RESULT:')]
        try:
            value = json.loads(lines[-1].split(':', 1)[1])
            if not isinstance(value, dict) or not isinstance(value.get('status'), str):
                raise ValueError()
            return value
        except (ValueError, IndexError):
            return {'status': 'sdk_error', 'message': 'The quote worker did not return a valid response'}


def classify_time(instant):
    local = instant.astimezone(NY)
    minute = local.hour * 60 + local.minute
    trade_day = (local.date() + timedelta(days=1) if minute >= 20 * 60 else local.date()).isoformat()
    if not calendar('US').is_session(trade_day):
        raise ValueError('Outside an eligible US exchange session')
    opening, closing = session_times('US', trade_day)
    if minute >= 20 * 60 or minute < 4 * 60:
        return 'overnight', trade_day
    if instant < opening:
        return 'pre-market', trade_day
    if instant < closing:
        return 'regular', trade_day
    return 'after-hours', trade_day


def normalise_bar(row, symbol, retrieved):
    if row.get('code') != 'US.' + symbol.removesuffix('.US'):
        raise ValueError('Wrong source symbol')
    local = datetime.strptime(row['time_key'], '%Y-%m-%d %H:%M:%S')
    start = local.replace(tzinfo=NY)
    if start.utcoffset() != local.replace(tzinfo=NY, fold=1).utcoffset() or start.astimezone(timezone.utc).astimezone(NY).replace(tzinfo=None) != local:
        raise ValueError('Ambiguous or nonexistent US Eastern timestamp')
    start = start.astimezone(timezone.utc)
    if local.minute % 5 or local.second:
        raise ValueError('Expected a 5-minute boundary')
    if start > retrieved:
        raise ValueError('Future bar rejected')
    end = start + timedelta(minutes=5)
    if end + timedelta(minutes=1) > retrieved:
        return None  # The last incomplete/not-yet-settled candle is not evidence.
    session, trade_day = classify_time(start)
    for key in ('open', 'high', 'low', 'close', 'volume'):
        if isinstance(row.get(key), bool) or not isinstance(row.get(key), (int, float)) or not math.isfinite(row[key]):
            raise ValueError('Invalid OHLCV field')
    bar = Bar(date=trade_day, **{k: row[k] for k in ('open', 'high', 'low', 'close', 'volume')})
    return {**bar.model_dump(exclude_none=True), 'symbol': symbol, 'start_at': stamp(start), 'end_at': stamp(end),
            'session': session, 'source_time_et': row['time_key'], 'currency': 'USD', 'interval': '5m',
            'price_basis': 'Unadjusted provider OHLC · AuType.NONE', 'provider': 'moomoo', 'version': VERSION}


def initialise_intraday(service):
    with service.db.session() as session:
        if not session.query(IntradayConfigRevision).first():
            symbols = [i.symbol for i in service.universe if i.market == 'US' and i.role != 'benchmark'][:10]
            value = {'enabled': False, 'symbols': symbols or [i.symbol for i in service.universe if i.market == 'US'][:1]}
            session.add(IntradayConfigRevision(created_at=stamp(), payload=value, hash=digest(value)))


def intraday_config(service):
    with service.db.session() as session:
        saved = session.query(IntradayConfigRevision).order_by(IntradayConfigRevision.id.desc()).first()
        return {**saved.payload, 'revision': saved.id, 'saved_at': saved.created_at}


def capture(service, symbols, kind='capture'):
    if service.settings.data_mode == 'demo':
        raise ValueError('Actual Moomoo requests are disabled in fixture mode')
    for symbol in symbols:
        if service.instrument(symbol).market != 'US':
            raise ValueError('Moomoo intraday monitoring accepts saved US instruments only')
    retrieved = now_utc()
    result = service.moomoo_provider.fetch(symbols)
    rows, summaries = [], []
    if result.get('status') == 'success':
        for symbol in symbols:
            group = result.get('symbols', {}).get('US.' + symbol.removesuffix('.US'), {})
            accepted, rejected, incomplete, seen = [], {}, 0, set()
            source_rows = group.get('rows', [])
            if not isinstance(source_rows, list) or len(source_rows) > 300:
                source_rows, rejected = [], {'Unsupported bar response': 1}
            for raw in source_rows:
                try:
                    bar = normalise_bar(raw, symbol, retrieved)
                    if bar is None:
                        incomplete += 1
                        continue
                    if bar['start_at'] in seen:
                        raise ValueError('Duplicate bar timestamp')
                    seen.add(bar['start_at'])
                    accepted.append(bar)
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    message = str(exc) if isinstance(exc, ValueError) else 'Invalid bar response'
                    rejected[message] = rejected.get(message, 0) + 1
            accepted.sort(key=lambda b: b['start_at'])
            rows.extend(accepted)
            summaries.append({'symbol': symbol, 'status': 'available' if accepted else 'unavailable',
                              'accepted': len(accepted), 'rejected': rejected, 'incomplete': incomplete,
                              'last_bar_end': accepted[-1]['end_at'] if accepted else None,
                              'sessions': {s: sum(b['session'] == s for b in accepted) for s in ('pre-market','regular','after-hours','overnight')}})
    status = 'success' if rows and all(s['accepted'] for s in summaries) else 'partial' if rows else result.get('status') if result.get('status') != 'success' else 'no_valid_bars'
    messages = {'success': 'Completed 5-minute bars validated. Observed session counts do not prove full-market coverage.',
                'partial': 'Some requested instruments were unavailable; inspect symbol results.',
                'no_valid_bars': 'Quote access responded but no completed eligible bars passed validation.',
                'gateway_unavailable': 'Open Moomoo OpenD and sign in locally; its quote gateway is not reachable.',
                'sdk_missing': 'Install backend/moomoo-requirements.txt and restart MarketLens.',
                'login_required': 'Sign into Moomoo OpenD and wait for quote-server login.',
                'subscription_denied': 'US quote subscription denied. Check OpenD API entitlements and quotas.',
                'timeout': 'OpenD quote request timed out.'}
    payload = {'checked_at': stamp(retrieved), 'status': status, 'message': messages.get(status, 'Moomoo quote request unavailable; no alternative source or fixture used.'),
               'provider': 'moomoo', 'mode': 'actual', 'version': VERSION, 'symbols': summaries,
               'requested_symbols': symbols, 'bar_hashes': [digest(b) for b in rows], 'new_bars': 0}
    with service.db.session() as session:
        if rows:
            earliest = min(b['start_at'] for b in rows)
            existing = {h for (h,) in session.query(IntradayBar.hash).filter(IntradayBar.provider == 'moomoo', IntradayBar.symbol.in_(symbols), IntradayBar.start_at >= earliest)}
            for bar in rows:
                key = digest(bar)
                if key not in existing:
                    session.add(IntradayBar(symbol=bar['symbol'], start_at=bar['start_at'], observed_at=stamp(retrieved), provider='moomoo', payload=bar, hash=key))
                    existing.add(key)
                    payload['new_bars'] += 1
        saved = IntradayAttempt(created_at=stamp(retrieved), kind=kind, status=status, payload=payload, hash=digest(payload))
        session.add(saved)
        session.flush()
        identifier = saved.id
    return {'attempt_id': identifier, **{k:v for k,v in payload.items() if k != 'bar_hashes'}}


def save_intraday_config(service, body):
    symbols = list(dict.fromkeys(body.symbols))
    for symbol in symbols:
        if service.instrument(symbol).market != 'US':
            raise ValueError('Select saved US instruments only')
    current = intraday_config(service)
    if body.expected_revision != current['revision']:
        raise UniverseConflict('Intraday settings changed. Reload before saving')
    if body.enabled:
        probe = capture(service, symbols, kind='activation')
        if probe['status'] != 'success':
            raise ValueError('Collection was not enabled: ' + probe['message'])
    value = {'enabled': body.enabled, 'symbols': symbols}
    with service.db.session() as session:
        latest = session.query(IntradayConfigRevision).order_by(IntradayConfigRevision.id.desc()).first()
        if latest.id != body.expected_revision:
            raise UniverseConflict('Intraday settings changed. Reload before saving')
        if latest.hash != digest(value):
            session.add(IntradayConfigRevision(created_at=stamp(), payload=value, hash=digest(value)))
    return intraday_config(service)


def intraday_view(service, symbol=None):
    config = intraday_config(service)
    symbols = [i.model_dump() for i in service.universe if i.market == 'US']
    if symbol and symbol not in {i['symbol'] for i in symbols}:
        raise ValueError('Select an included US instrument')
    selected = symbol or next(iter(config['symbols']), 'AAPL.US')
    with service.db.session() as session:
        attempt = session.query(IntradayAttempt).order_by(IntradayAttempt.id.desc()).first()
        # Latest vendor revision per timestamp; every old version remains in DB.
        cached = session.query(IntradayBar).filter_by(symbol=selected, provider='moomoo').order_by(IntradayBar.start_at.desc(), IntradayBar.id.desc()).limit(1200)
        by_time = {}
        for row in cached:
            by_time.setdefault(row.start_at, {**row.payload, 'observed_at': row.observed_at})
        bars = sorted(by_time.values(), key=lambda b: b['start_at'])[-300:]
    try:
        current_session, _ = classify_time(now_utc())
    except ValueError:
        current_session = 'market closed'
    latest = bars[-1]['end_at'] if bars else None
    freshness = 'unavailable' if not bars else 'market closed' if current_session == 'market closed' else 'stale' if (now_utc() - parse_time(latest)).total_seconds() > 900 else 'recent completed bars'
    return {'provider': INFO, 'config': config, 'instruments': symbols, 'symbol': selected, 'bars': bars,
            'sdk_installed': sdk_available(), 'gateway': '127.0.0.1:' + str(service.settings.moomoo_opend_port),
            'last_attempt': {'attempt_id': attempt.id, **{k:v for k,v in attempt.payload.items() if k != 'bar_hashes'}} if attempt else None,
            'current_session': current_session, 'freshness': freshness, 'last_bar_end': latest,
            'mode': 'actual' if bars else 'unavailable', 'requests_enabled': service.settings.data_mode != 'demo', 'local_only': True}


def intraday_tick(service):
    if not service.operations.acquire(blocking=False):
        return {'status': 'busy'}
    try:
        config = intraday_config(service)
        if not config['enabled']:
            return {'status': 'disabled'}
        try:
            classify_time(now_utc())
        except ValueError:
            return {'status': 'market_closed'}
        return service.job('moomoo:capture', lambda: capture(service, config['symbols']))
    finally:
        service.operations.release()
