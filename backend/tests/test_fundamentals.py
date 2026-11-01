"""Explicit synthetic company-profile fixtures, never actual market evidence."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from marketlens.config import Settings
from marketlens.db import Database, Dataset, FundamentalSnapshot, Signal
from marketlens.discovery import DiscoveryRequest, YahooDiscovery, run_discovery
from marketlens.domain import now_utc
from marketlens.fundamentals import ProfileRateLimit, analyst, filter_failure, profile_payload
from marketlens.service import Service


def quote(symbol='NEW.AX', **extra):
    return {'symbol':symbol,'shortName':'Explicit fixture','exchange':'ASX','currency':'AUD','quoteType':'EQUITY',
            'regularMarketPrice':25.,'regularMarketVolume':2_000_000,'averageDailyVolume3Month':1_000_000,
            'regularMarketTime':(now_utc()-timedelta(days=1)).timestamp(),
            'marketCap':2_000_000_000,'averageAnalystRating':'2.4 - Buy',**extra}


def profile(symbol='NEW.AX', **extra):
    return {'symbol':symbol,'quoteType':'EQUITY','currency':'AUD','financialCurrency':'USD',
            'marketCap':1_900_000_000,'returnOnEquity':.24,'recommendationMean':2.2,
            'recommendationKey':'buy','numberOfAnalystOpinions':16,
            'mostRecentQuarter':(now_utc()-timedelta(days=90)).timestamp(),**extra}


@pytest.fixture
def company_service(tmp_path, monkeypatch):
    cfg=Settings(_env_file=None,data_mode='yahoo',database_url=f'sqlite:///{tmp_path.as_posix()}/company.db',
                 report_dir=tmp_path/'reports',discovery_fundamental_budget=1,scheduler_enabled=False,publish_enabled=False)
    svc=Service(cfg,Database(cfg.database_url))
    svc.initialise()
    monkeypatch.setattr('marketlens.discovery.source_status',lambda r,lag=180:'current')
    return svc


@pytest.mark.parametrize('rating,key,mean',[('1.3 - Strong Buy','strong_buy',1.3),('2.5 - Buy','buy',2.5),('2.6 - Hold','hold',2.6),('4.5 - Sell','sell',4.5)])
def test_literal_provider_consensus_is_preserved(rating,key,mean):
    result=analyst({'averageAnalystRating':rating})
    assert result['analyst_rating']==key and result['analyst_mean']==mean
    assert result['analyst_count'] is None


@pytest.mark.parametrize('value',[{}, {'averageAnalystRating':'0 - None'}, {'recommendationMean':float('nan'),'recommendationKey':'buy'},
                                 {'averageAnalystRating':'4 stars'}, {'recommendationMean':2,'recommendationKey':['buy']},
                                 {'averageAnalystRating':'5.5 - Sell'}])
def test_missing_invalid_and_fund_star_ratings_are_not_invented(value):
    assert analyst(value)=={'analyst_rating':None,'analyst_mean':None,'analyst_count':None}


def test_zero_analyst_coverage_is_not_presented_as_a_current_buy_consensus():
    assert analyst({'recommendationKey':'buy','recommendationMean':2.1,'numberOfAnalystOpinions':0}) == {'analyst_rating':None,'analyst_mean':None,'analyst_count':0}


def test_roe_ratio_is_percentage_and_cap_uses_quote_currency_not_accounting_currency():
    row={'source_symbol':'NEW.AX','symbol':'NEW.AU','currency':'AUD'}
    value=profile_payload(profile(),row,now_utc())
    assert value['roe_percent']==24 and value['currency']=='AUD'
    assert profile_payload(profile(returnOnEquity=-.1),row,now_utc())['roe_percent']==-10
    assert profile_payload(profile(returnOnEquity=1.48),row,now_utc())['roe_percent']==148
    assert profile_payload(profile(returnOnEquity=0),row,now_utc())['roe_percent']==0
    assert profile_payload(profile(returnOnEquity=float('inf')),row,now_utc())['roe_percent'] is None
    with pytest.raises(ValueError,match='did not match'):
        profile_payload(profile(symbol='WRONG'),row,now_utc())


def test_every_active_filter_excludes_unknown_and_zero_roe_is_real():
    assert filter_failure({},DiscoveryRequest(min_market_cap=0).effective())
    assert filter_failure({'market_cap':1_000_000_000},DiscoveryRequest(min_market_cap=500_000_000).effective()) is None
    assert filter_failure({'roe_percent':0},DiscoveryRequest(min_roe_percent=0).effective()) is None
    assert filter_failure({'roe_percent':None},DiscoveryRequest(min_roe_percent=0).effective())
    assert filter_failure({'analyst_rating':'hold'},DiscoveryRequest(analyst_rating='buy_or_better').effective())
    assert filter_failure({'analyst_rating':'buy'},DiscoveryRequest(analyst_rating='buy_or_better').effective()) is None
    assert filter_failure({},DiscoveryRequest().effective()) is None


@pytest.mark.parametrize('body',[{'min_market_cap':2,'max_market_cap':1}, {'min_roe_percent':20,'max_roe_percent':10},
                                {'min_market_cap':-1}, {'analyst_rating':'five_stars'}, {'min_roe_percent':float('nan')}])
def test_invalid_filter_ranges_and_nonfinite_requests_rejected(body):
    with pytest.raises(ValueError):
        DiscoveryRequest(**body)


def test_bounded_profiles_filter_only_verified_rows_then_reuse_cache_after_restart(company_service):
    svc=company_service
    calls=[]
    svc.discovery_provider=SimpleNamespace(screen=lambda req,kind:{'quotes':[quote(),quote('SECOND.AX')],'total':2},
                                           fundamentals=lambda symbol:calls.append(symbol) or profile(symbol))
    first=run_discovery(svc,DiscoveryRequest(kind='stock',min_roe_percent=10,analyst_rating='buy_or_better'))
    assert len(first['rows'])==1 and len(calls)==1
    row=first['rows'][0]
    assert row['roe_percent']==24 and row['analyst_count']==16 and row['analyst_mean']==2.2
    assert row['market_cap']==2_000_000_000 and row['market_cap_currency']=='AUD'
    assert first['groups'][0]['fundamentals']['not_checked']==1
    assert first['groups'][0]['skipped']['ROE unavailable or not applicable for active filter']==1
    assert first['request']['fundamental_profile_budget']==1
    assert 'ROE 24.00%' in row['reason']
    restarted=Service(svc.settings,Database(svc.settings.database_url)); restarted.initialise()
    restarted.discovery_provider=svc.discovery_provider
    second=run_discovery(restarted,DiscoveryRequest(kind='stock',limit=50,min_roe_percent=10))
    assert len(second['rows'])==2 and len(calls)==2
    assert second['groups'][0]['fundamentals']['cached']==1
    with svc.db.session() as session:
        assert session.query(FundamentalSnapshot).count()==2
        assert session.query(Dataset).count()==session.query(Signal).count()==0
    with pytest.raises(Exception,match='immutable'):
        with svc.db.engine.begin() as conn: conn.execute(text('DELETE FROM fundamental_snapshots'))


def test_failed_profiles_do_not_replace_values_or_pass_roe_filter(company_service):
    svc=company_service
    calls=[]
    def failure(symbol):
        calls.append(symbol); raise ValueError('SECRET provider diagnostic')
    svc.discovery_provider=SimpleNamespace(screen=lambda *a:{'quotes':[quote()],'total':1},fundamentals=failure)
    result=run_discovery(svc,DiscoveryRequest(kind='stock',min_roe_percent=10))
    assert result['rows']==[] and result['groups'][0]['fundamentals']['failed']==1
    next_result=run_discovery(svc,DiscoveryRequest(kind='stock',limit=50))
    assert len(calls)==1 and next_result['rows'][0]['roe_percent'] is None
    assert next_result['rows'][0]['analyst_rating']=='buy' and 'SECRET' not in str(next_result)


def test_expired_profiles_are_not_used_when_refresh_fails(company_service,monkeypatch):
    svc=company_service
    svc.discovery_provider=SimpleNamespace(screen=lambda *a:{'quotes':[quote()],'total':1},fundamentals=lambda s:profile(s))
    assert len(run_discovery(svc,DiscoveryRequest(kind='stock',min_roe_percent=10))['rows'])==1
    future=now_utc()+timedelta(days=2)
    monkeypatch.setattr('marketlens.discovery.now_utc',lambda:future)
    monkeypatch.setattr('marketlens.domain.now_utc',lambda:future)
    svc.discovery_provider.fundamentals=lambda s: (_ for _ in ()).throw(ValueError('failed'))
    result=run_discovery(svc,DiscoveryRequest(kind='stock',limit=50,min_roe_percent=10))
    assert result['rows']==[] and result['groups'][0]['fundamentals']['cached']==0


def test_etf_assets_and_fund_ratings_are_not_company_metrics(company_service):
    svc=company_service
    svc.discovery_provider=SimpleNamespace(screen=lambda *a:{'quotes':[quote('ETF.AX',quoteType='ETF',marketCap=None,averageAnalystRating=None,totalAssets=50e9,morningstarOverallRating=5)],'total':1},
                                           fundamentals=lambda s:pytest.fail('No company request for ETF'))
    result=run_discovery(svc,DiscoveryRequest(kind='etf',min_market_cap=500e6,min_roe_percent=0,analyst_rating='rated'))
    assert result['rows']==[] and result['groups'][0]['fundamentals']['requested']==0


def test_market_cap_bounds_are_sent_to_stock_screener_not_etf_query(monkeypatch):
    import yfinance as yf
    requests=[]
    monkeypatch.setattr(yf,'screen',lambda q,**kw:requests.append(q.to_dict()) or {'quotes':[],'total':0})
    adapter=YahooDiscovery(Settings(_env_file=None))
    monkeypatch.setattr(adapter,'pace',lambda:None)
    request=DiscoveryRequest(min_market_cap=500e6,max_market_cap=5e9).effective()
    adapter.screen(request,'stock'); adapter.screen(request,'etf')
    assert 'intradaymarketcap' in str(requests[0]) and '500000000' in str(requests[0])
    assert 'intradaymarketcap' not in str(requests[1])


def test_rate_limit_stops_requests_and_persists_cooldown_across_restart(company_service):
    svc=company_service
    svc.settings.discovery_fundamental_budget=20
    calls=[]
    def limited(symbol):
        calls.append(symbol); raise ProfileRateLimit('rate limited')
    svc.discovery_provider=SimpleNamespace(screen=lambda *a:{'quotes':[quote(),quote('SECOND.AX')],'total':2},fundamentals=limited)
    first=run_discovery(svc,DiscoveryRequest(kind='stock',min_roe_percent=0))
    assert len(calls)==1 and first['groups'][0]['fundamentals']['rate_limited']
    restarted=Service(svc.settings,Database(svc.settings.database_url)); restarted.initialise()
    restarted.discovery_provider=svc.discovery_provider
    second=run_discovery(restarted,DiscoveryRequest(kind='stock',limit=50,min_roe_percent=0))
    assert len(calls)==1 and second['rows']==[] and second['groups'][0]['fundamentals']['rate_limited']
