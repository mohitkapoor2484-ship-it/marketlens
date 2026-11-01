import { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Check, RefreshCw, Search, Telescope } from 'lucide-react';
import { api, Data, isPagesPreview, localTime, num } from './api';
import './discovery.css';

const presets: Record<string, Data> = {
  ASX: { min_price: .3, min_average_volume: 100000, min_turnover: 1000000 },
  US: { min_price: 5, min_average_volume: 300000, min_turnover: 5000000 }
};

export function Discovery({ disabled, onBusy, onSaved, openStock }: {
  disabled: boolean, onBusy: (message: string) => void, onSaved: () => Promise<void>, openStock: (symbol: string) => void
}) {
  const [data, setData] = useState<Data>();
  const [market, setMarket] = useState('ASX');
  const [form, setForm] = useState<Data>({ kind: 'all', limit: 250, ...presets.ASX });
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [onlyNew, setOnlyNew] = useState(true);
  const [trend, setTrend] = useState(false);
  const [sort, setSort] = useState('relative_volume_3m');
  const [page, setPage] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [result, setResult] = useState<Data>();
  const reload = useCallback(async () => { setData(await api('/discovery')); }, []);
  useEffect(() => {
    void reload().catch(e => setError(e.message));
    const timer = window.setInterval(() => void reload().catch(e => setError(e.message)), 60000);
    return () => window.clearInterval(timer);
  }, [reload]);
  const run = data?.markets.find((m: Data) => m.market === market);
  const runId = run?.run_id;
  useEffect(() => { setSelected([]); setPage(0); }, [market, runId, data?.universe_revision]);
  useEffect(() => setPage(0), [query, onlyNew, trend, sort]);
  const rows: Data[] = (run?.rows || []).filter((r: Data) => (!onlyNew || !r.in_universe) && (!trend || r.above_sma200 === true) && `${r.symbol} ${r.name}`.toLowerCase().includes(query.toLowerCase()));
  rows.sort((a, b) => (b[sort] ?? -Infinity) - (a[sort] ?? -Infinity) || a.symbol.localeCompare(b.symbol));
  const eligible = rows.filter(r => !r.in_universe && r.quote_status === 'current');
  const blocked = disabled || pending;

  async function discover() {
    setPending(true); setError(''); setMessage(''); setResult(undefined); onBusy(`Discovering ${market} stocks and ETFs`);
    try {
      const found = await api('/discovery', 'POST', { market, ...form });
      setMessage(`${found.cached ? 'Reused the saved request within its 30-minute cache.' : 'Discovery saved.'} ${found.rows.length} candidates passed the discovery filters. Full EOD setup qualification is a separate step.`);
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : 'Discovery failed'); await reload().catch(() => {}); }
    finally { setPending(false); onBusy(''); }
  }
  async function add() {
    setPending(true); setError(''); setMessage(''); setResult(undefined); onBusy(`Downloading daily history for ${selected.length} selected candidates`);
    try {
      const response = await api('/discovery/import', 'POST', { run_id: runId, symbols: selected, expected_revision: data?.universe_revision });
      setResult(response); setSelected([]); await onSaved(); await reload();
    } catch (e) { setError(e instanceof Error ? e.message : 'Import failed'); await reload().catch(() => {}); }
    finally { setPending(false); onBusy(''); }
  }
  function toggle(symbol: string) {
    setSelected(old => old.includes(symbol) ? old.filter(s => s !== symbol) : old.length < 20 ? [...old, symbol] : old);
  }
  return <>
    <div className="info-strip"><Telescope size={22}/><span><b>Find ideas beyond your saved list.</b> Yahoo discovers a bounded sample of ASX and US stocks/ETFs. Add selected candidates to download daily history and check the existing setup rules. Discovery statistics do not qualify a trade or generate paper signals.</span></div>
    <section className="panel">
      <div className="panel-head"><div><h2>Discover candidates</h2><p>Yahoo Finance screener · source-currency filters · completed-session eligibility</p></div><span className="badge">{isPagesPreview ? 'Published · read-only' : 'Research only'}</span></div>
      <div className="padded">
        <div className="discovery-tabs" role="group" aria-label="Discovery market">{['ASX', 'US'].map(m => <button key={m} className={`button ${market === m ? '' : 'secondary'}`} disabled={blocked} onClick={() => { setMarket(m); setForm({ ...form, ...presets[m] }); setError(''); setMessage(''); setResult(undefined); }}>{m === 'ASX' ? 'ASX · AUD' : 'US · USD'}</button>)}</div>
        {!isPagesPreview && <form className="universe-add discovery-form" onSubmit={e => { e.preventDefault(); void discover(); }}>
          <label>Find<select aria-label="Discovery asset type" disabled={blocked} value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}><option value="all">Stocks & ETFs</option><option value="stock">Stocks</option><option value="etf">ETFs</option></select></label>
          <label>Limit per type<select aria-label="Discovery limit" disabled={blocked} value={form.limit} onChange={e => setForm({ ...form, limit: Number(e.target.value) })}>{[25, 50, 100, 250].map(n => <option key={n}>{n}</option>)}</select></label>
          {['min_price', 'min_average_volume', 'min_turnover'].map((k, i) => <label key={k}>{['Minimum price', 'Average volume ≥', 'Approx. daily turnover ≥'][i]}<input aria-label={`Discovery ${k}`} required type="number" min={i === 0 ? '.01' : '0'} max={i === 0 ? '100000' : i === 1 ? '1000000000000' : '1000000000000000'} step="any" disabled={blocked} value={form[k]} onChange={e => setForm({ ...form, [k]: Number(e.target.value) })}/></label>)}
          <button className="button" type="submit" disabled={blocked || data?.mode !== 'yahoo' || !data?.enabled}><Search size={15}/>Discover now</button>
        </form>}
        <p className="muted">{isPagesPreview ? 'Run discovery and add instruments on the computer running MarketLens. Results arrive with the next successful five-minute upload; this public page cannot start a local scan.' : 'Scheduled discovery runs after exchange-close research cycles. Manual identical requests reuse saved results for 30 minutes. Selection downloads at most 20 histories; your saved universe changes only when you add them.'}</p>
        {!data && !error && <p role="status" className="muted">Loading saved discovery results…</p>}
        {data && data.mode !== 'yahoo' && <p className="amber-text">Broad discovery requires Yahoo mode. No actual discovery data is generated from fixtures or substituted from another provider.</p>}
        {data?.notice && <p className="amber-text">{data.notice}</p>}
        {error && <p role="alert" className="amber-text">{error}</p>}
        {message && <p role="status" className="green-text">{message}</p>}
        {result && <div role="status" className="discovery-result"><b>{result.added.length} added · {result.successful.length} downloads succeeded · {result.failed.length} failed</b><p>{result.notice}</p>{result.failed.length > 0 && <p className="amber-text">Failed feeds: {result.failed.join(', ')}. Saved instruments remain visible as unavailable; retry via Refresh & upload now.</p>}<p>Open Market Scanner for computed scores, qualifying setups and exclusions.</p></div>}
      </div>
    </section>
    <section className="panel">
      <div className="panel-head"><div><h2>{market} discovery results</h2><p>{run?.created_at ? `Snapshot ${localTime(run.created_at)} · run ${runId}` : 'No successful discovery saved yet'}</p></div><button className="icon-button" aria-label="Reload saved discovery" disabled={blocked} onClick={() => void reload().catch(e => setError(e.message))}><RefreshCw size={16}/></button></div>
      {run?.last_attempt && run.last_attempt.status !== 'success' && <div className="padded amber-text" role="status">Latest attempt: {run.last_attempt.status} · {localTime(run.last_attempt.at)}. {run.last_attempt.errors.join(' · ')} {run.status === 'failed' && runId && 'Showing the previous successful snapshot.'}</div>}
      {run?.groups && <div className="discovery-counts padded">{run.groups.map((g: Data) => <div key={g.kind}><b>{g.kind === 'stock' ? 'Stocks' : 'ETFs'}</b><span>{g.status === 'success' ? `${g.provider_matches == null ? 'Unknown' : num(g.provider_matches, 0)} provider matches · ${g.returned} returned · ${g.accepted} passed` : 'Source unavailable'}</span>{g.capped && <small className="amber-text">Result cap reached · sampling by average volume</small>}{g.skipped && Object.keys(g.skipped).length > 0 && <details><summary>{Object.values(g.skipped).reduce((a: any, b: any) => a + b, 0) as number} excluded rows</summary>{Object.entries(g.skipped).map(([reason, n]) => <small key={reason}>{String(n)} · {reason}</small>)}</details>}</div>)}</div>}
      <div className="filter-bar discovery-filters"><label className="search-field"><Search size={16}/><input aria-label="Search discovered candidates" placeholder="Ticker or company" value={query} onChange={e => setQuery(e.target.value)}/></label><select aria-label="Sort discovery" value={sort} onChange={e => setSort(e.target.value)}><option value="relative_volume_3m">Relative volume</option><option value="approximate_turnover">Approximate turnover</option><option value="change_percent">Day change</option></select><label className="check-label"><input type="checkbox" checked={onlyNew} onChange={e => setOnlyNew(e.target.checked)}/>New to universe</label><label className="check-label"><input type="checkbox" checked={trend} onChange={e => setTrend(e.target.checked)}/>Above provider SMA200</label></div>
      {!isPagesPreview && <div className="padded action-row"><button className="button" disabled={blocked || !selected.length} onClick={() => void add()}><ArrowRight size={15}/>Add & analyse selected ({selected.length}/20)</button><button className="button secondary" disabled={blocked || !eligible.length} onClick={() => setSelected(eligible.slice(0, 20).map(r => r.symbol))}>Select first {Math.min(20, eligible.length)} eligible</button>{selected.length > 0 && <button className="text-button" disabled={blocked} onClick={() => setSelected([])}>Clear selection</button>}</div>}
      {rows.length ? <><div className="table-scroll"><table className="discovery-table"><thead><tr>{!isPagesPreview && <th>Select</th>}<th>Candidate</th><th>Quote snapshot</th><th>Relative volume</th><th>Approx. turnover</th><th>Provider trend</th><th>Data / action</th></tr></thead><tbody>{rows.slice(page * 25, page * 25 + 25).map(r => <tr key={r.symbol}>
        {!isPagesPreview && <td><input type="checkbox" aria-label={`Select ${r.symbol}`} disabled={blocked || r.in_universe || r.quote_status !== 'current' || selected.length >= 20 && !selected.includes(r.symbol)} checked={selected.includes(r.symbol)} onChange={() => toggle(r.symbol)}/></td>}
        <td><b>{r.symbol}</b><small>{r.name}</small><span className="badge">{r.kind.toUpperCase()}</span>{r.in_universe && <small className="green-text"><Check size={12}/>In universe</small>}</td>
        <td>{r.currency} {num(r.quote_price, r.quote_price < 1 ? 4 : 2)}<small className={(r.change_percent ?? 0) >= 0 ? 'green-text' : 'red-text'}>{num(r.change_percent)}% day</small><small>{localTime(r.source_at)}</small></td>
        <td>{num(r.relative_volume_3m)}×<small>{num(r.volume, 0)} shares</small><small>vs 3-month avg {num(r.average_volume_3m, 0)}</small></td>
        <td>{r.currency} {num(r.approximate_turnover, 0)}<small>Quote × 3-month avg volume</small></td>
        <td>{r.above_sma50 == null ? 'SMA50 unavailable' : r.above_sma50 ? 'Above SMA50' : 'Below SMA50'}<small>{r.above_sma200 == null ? 'SMA200 unavailable' : r.above_sma200 ? 'Above SMA200' : 'Below SMA200'}</small><small>Discovery context only</small></td>
        <td><span className={`badge ${r.quote_status === 'current' ? '' : 'amber'}`}>{r.quote_status === 'current' ? 'Delayed snapshot' : r.quote_status}</span>{r.in_universe ? <button className="text-button" onClick={() => openStock(r.symbol)}>Open analysis <ArrowRight size={12}/></button> : <small>Full EOD setup analysis pending</small>}<details><summary>Why included</summary><small>{r.reason}</small></details></td>
      </tr>)}</tbody></table></div><div className="panel-foot discovery-pagination"><span>{rows.length} matching candidates · no setup score assigned</span><button className="button secondary" disabled={!page} onClick={() => setPage(p => p - 1)}>Previous</button><span>{page + 1} / {Math.ceil(rows.length / 25)}</span><button className="button secondary" disabled={(page + 1) * 25 >= rows.length} onClick={() => setPage(p => p + 1)}>Next</button></div></> : <div className="empty"><Telescope size={28}/><h3>{runId ? 'No matching discovery candidates' : 'Ready to discover'}</h3><p>{runId ? 'Adjust the display filters or run a new discovery with different source filters. No candidates are forced into this list.' : isPagesPreview ? 'Discovery results appear after the local engine runs and uploads them.' : 'Choose a market and press Discover now. Yahoo availability and supported history determine what can be analysed.'}</p></div>}
    </section>
    <section className="panel padded"><h2>Understand this search</h2><p className="muted">The source returns up to {run?.request?.limit || form.limit} per selected asset type, ordered by three-month average share volume. Local price, volume and approximate-turnover checks then apply. This page sorts the accepted sample by your selected field; it does not rank the whole exchange. Source values may be delayed, stale or unavailable. ETF leverage, inverse exposure and holdings need separate review.</p><p className="muted">Full research uses Yahoo daily history, Google News RSS and your existing six deterministic setup rules. Missing history, gaps, warmup and liquidity can exclude discovered names. Current snapshots do not establish point-in-time historical membership.</p><a href="https://ranaroussi.github.io/yfinance/reference/api/yfinance.screen.html" target="_blank" rel="noreferrer">Screener documentation</a><p className="muted">Unofficial Yahoo client for personal research; no guaranteed coverage, delay, quota or data redistribution licence.</p></section>
  </>;
}
