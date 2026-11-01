"""Timestamped Yahoo company metrics, separate from price/strategy evidence."""
import math
import re
from datetime import datetime, timezone

from .db import FundamentalSnapshot
from .domain import digest, parse_time, stamp
from .providers import ProviderError

COOLDOWN = '__yahoo_company_profile_cooldown__'


class ProfileRateLimit(ProviderError):
    pass

RATINGS = {'strong_buy': 'Strong Buy', 'buy': 'Buy', 'hold': 'Hold',
           'underperform': 'Underperform', 'sell': 'Sell', 'strong_sell': 'Strong Sell'}
RATING_FILTERS = {'any': set(RATINGS), 'rated': set(RATINGS), 'strong_buy': {'strong_buy'},
                  'buy_or_better': {'strong_buy','buy'}, 'hold_or_better': {'strong_buy','buy','hold'},
                  'underperform_or_sell': {'underperform','sell','strong_sell'}}


def finite(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return None
    return float(value) if not positive or value > 0 else None


def analyst(value):
    """Preserve the provider's category; never infer an analyst opinion."""
    count = finite(value.get('numberOfAnalystOpinions'))
    count = int(count) if count is not None and count >= 0 and count.is_integer() else None
    missing = {'analyst_rating': None, 'analyst_mean': None, 'analyst_count': count}
    if count == 0:
        return missing
    mean = finite(value.get('recommendationMean'))
    key = value.get('recommendationKey')
    if not isinstance(key, str) or key not in RATINGS or mean is None or not 1 <= mean <= 5:
        text = value.get('averageAnalystRating')
        match = re.fullmatch(r'([1-5](?:\.\d+)?)\s*-\s*(Strong Buy|Buy|Hold|Underperform|Sell|Strong Sell)', text, re.I) if isinstance(text, str) else None
        if not match or not 1 <= float(match[1]) <= 5:
            return missing
        mean, key = float(match[1]), match[2].lower().replace(' ', '_')
    return {'analyst_rating': key, 'analyst_mean': mean, 'analyst_count': count}


def profile_payload(value, instrument, fetched):
    if not isinstance(value, dict) or value.get('symbol') != instrument['source_symbol'] or value.get('quoteType') != 'EQUITY' or value.get('currency') != instrument['currency']:
        raise ValueError('Company profile symbol, type or quote currency did not match')
    roe = finite(value.get('returnOnEquity'))
    quarter = finite(value.get('mostRecentQuarter'), positive=True)
    period = None
    if quarter is not None:
        try:
            day = datetime.fromtimestamp(quarter, timezone.utc)
            if day <= fetched:
                period = day.date().isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return {'symbol': instrument['symbol'], 'source_symbol': instrument['source_symbol'],
            'provider': 'Yahoo Finance company profile via yfinance', 'fetched_at': stamp(fetched),
            'roe_percent': roe * 100 if roe is not None and math.isfinite(roe * 100) else None,
            'market_cap': finite(value.get('marketCap'), positive=True), 'currency': instrument['currency'],
            **analyst(value), 'reported_quarter': period,
            'period_basis': 'Yahoo mostRecentQuarter metadata; exact ROE/analyst publication time not supplied',
            'roe_basis': 'Provider returnOnEquity ratio × 100; trailing twelve months', 'version': 'yahoo-company-metrics-1'}


def enrich_rows(service, rows, retrieved):
    """Use all fresh cached profiles, with a bounded number of new source calls."""
    budget = service.settings.discovery_fundamental_budget
    stats = {'requested': 0, 'cached': 0, 'failed': 0, 'not_checked': 0, 'roe_available': 0, 'rate_limited': False}
    with service.db.session() as session:
        snapshots = session.query(FundamentalSnapshot).filter(FundamentalSnapshot.symbol.in_([r['symbol'] for r in rows])).order_by(FundamentalSnapshot.fetched_at.desc()).all()
        cached = {}
        for snapshot in snapshots:
            cached.setdefault(snapshot.symbol, snapshot)
        cooldown = session.query(FundamentalSnapshot).filter_by(symbol=COOLDOWN).order_by(FundamentalSnapshot.fetched_at.desc()).first()
        blocked = bool(cooldown and 0 <= (retrieved-parse_time(cooldown.fetched_at)).total_seconds() < 1800)
        stats['rate_limited'] = blocked
    fetcher = getattr(service.discovery_provider, 'fundamentals', None)
    for row in rows:
        if row['kind'] != 'stock':
            row['fundamental_status'] = 'not applicable: ETF company metrics'
            continue
        prior = cached.get(row['symbol'])
        payload, source_id = None, None
        if prior and 0 <= (retrieved - parse_time(prior.fetched_at)).total_seconds() < (86400 if prior.status == 'available' else 1800):
            stats['cached'] += 1
            if prior.status == 'available':
                payload, source_id = prior.payload, prior.id
            else:
                row['fundamental_status'] = 'unavailable: recent profile request failed'
        elif not blocked and callable(fetcher) and stats['requested'] < budget:
            stats['requested'] += 1
            try:
                value = fetcher(row['source_symbol'])
                from .domain import now_utc
                fetched = now_utc()
                payload = profile_payload(value, row, fetched)
                status = 'available'
            except Exception as exc:
                from .domain import now_utc
                fetched, status = now_utc(), 'unavailable'
                payload = {'symbol': row['symbol'], 'source_symbol': row['source_symbol'],
                           'provider': 'Yahoo Finance company profile via yfinance', 'fetched_at': stamp(fetched),
                           'notice': 'Profile unavailable or invalid; no synthetic or alternative source used'}
                stats['failed'] += 1
                if isinstance(exc, ProfileRateLimit):
                    blocked, stats['rate_limited'] = True, True
                    cooldown_payload = {'provider': 'Yahoo company profile', 'notice': 'HTTP rate limit; pause new profile lookups for 30 minutes', 'fetched_at': stamp(fetched)}
                    cooldown_id = digest(cooldown_payload)
                    with service.db.session() as session:
                        if not session.get(FundamentalSnapshot, cooldown_id):
                            session.add(FundamentalSnapshot(id=cooldown_id, symbol=COOLDOWN, fetched_at=stamp(fetched), status='rate_limited', payload=cooldown_payload, hash=digest(cooldown_payload)))
            key = digest({'symbol': row['symbol'], 'fetched_at': stamp(fetched), 'payload': payload})
            with service.db.session() as session:
                if not session.get(FundamentalSnapshot, key):
                    session.add(FundamentalSnapshot(id=key, symbol=row['symbol'], fetched_at=stamp(fetched), status=status, payload=payload, hash=digest(payload)))
            if status == 'available':
                source_id = key
            else:
                payload = None
                row['fundamental_status'] = 'unavailable: profile request failed'
        else:
            stats['not_checked'] += 1
            row['fundamental_status'] = 'unavailable: provider rate-limit cooldown' if blocked else 'not checked: profile budget reached' if callable(fetcher) else 'unavailable: profile adapter missing'
        if payload:
            row.update(roe_percent=payload['roe_percent'], fundamental_fetched_at=payload['fetched_at'],
                       fundamental_snapshot_id=source_id, reported_quarter=payload['reported_quarter'],
                       roe_basis=payload['roe_basis'], fundamental_status='available' if payload['roe_percent'] is not None else 'unavailable: ROE not supplied')
            # Prefer a complete profile consensus when present, retain quote consensus otherwise.
            if payload['analyst_rating'] is not None or payload['analyst_count'] == 0:
                row.update({k: payload[k] for k in ('analyst_rating','analyst_mean','analyst_count')})
                row['analyst_source'] = 'Yahoo company profile'
                row['analyst_fetched_at'] = payload['fetched_at']
            if row.get('market_cap') is None and payload['market_cap'] is not None:
                row['market_cap'] = payload['market_cap']
                row['market_cap_source'] = 'Yahoo company profile'
                row['market_cap_fetched_at'] = payload['fetched_at']
        if row.get('roe_percent') is not None:
            stats['roe_available'] += 1
    return stats


def filter_failure(row, request):
    for field, low, high, label in [('market_cap','min_market_cap','max_market_cap','Market cap'),
                                  ('roe_percent','min_roe_percent','max_roe_percent','ROE')]:
        lower, upper = request.get(low), request.get(high)
        value = row.get(field)
        if lower is not None or upper is not None:
            if value is None:
                return f'{label} unavailable or not applicable for active filter'
            if lower is not None and value < lower or upper is not None and value > upper:
                return f'Below/above selected {label.lower()} range'
    rating = request.get('analyst_rating', 'any')
    if rating != 'any':
        if row.get('analyst_rating') not in RATING_FILTERS[rating]:
            return 'Analyst consensus unavailable or outside selected categories'
    return None
