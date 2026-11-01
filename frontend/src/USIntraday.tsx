import { useCallback, useEffect, useState } from 'react';
import { Activity, LockKeyhole, PlugZap, RefreshCw, Save } from 'lucide-react';
import { api, Data, isPagesPreview, localTime, num } from './api';
import { IntradayCandles } from './Charts';
import './intraday.css';

export function USIntraday({ disabled, onBusy }: { disabled: boolean, onBusy: (message: string) => void }) {
  const [data, setData] = useState<Data>();
  const [symbol, setSymbol] = useState('');
  const [draft, setDraft] = useState<Data>();
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const reload = useCallback(async () => {
    const value = await api('/intraday' + (symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''));
    setData(value);
    return value;
  }, [symbol]);
  useEffect(() => {
    void reload().catch(e => setError(e.message));
    const timer = window.setInterval(() => void reload().catch(e => setError(e.message)), 60000);
    return () => window.clearInterval(timer);
  }, [reload]);
  useEffect(() => { if (data?.config && !dirty) setDraft(data.config); }, [data, dirty]);
  const blocked = disabled || pending;
  const bars: Data[] = data?.bars || [];
  const last = data?.last_attempt;
  const good = last?.status === 'success';

  async function action(kind: string) {
    setPending(true); onBusy(kind === 'probe' ? 'Testing the local Moomoo quote gateway' : kind === 'save' ? 'Validating and saving US collection settings' : 'Refreshing US 5-minute bars');
    setError(''); setMessage('');
    try {
      if (kind === 'save') {
        await api('/intraday/config', 'PUT', { enabled: draft?.enabled, symbols: draft?.symbols, expected_revision: data?.config.revision });
        setDirty(false); setMessage('Collection settings saved. The five-minute collector runs while both MarketLens and OpenD are running, during eligible US sessions.');
      } else {
        const result = await api(kind === 'probe' ? '/intraday/probe' : '/intraday/refresh', 'POST', kind === 'probe' ? { symbol: symbol || data?.symbol } : undefined);
        setMessage(`${result.status === 'success' ? 'Quote test passed.' : 'Quote test did not fully pass.'} ${result.message}`);
      }
      const value = await reload();
      if (kind === 'save') setDraft(value.config);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Moomoo quote operation failed');
      if (kind === 'save') { const value = await reload(); setDraft(value.config); setDirty(false); }
    } finally { setPending(false); onBusy(''); }
  }
  if (isPagesPreview) return <section className="panel"><div className="panel-head"><div><h2>US intraday · local quote feed</h2><p>Moomoo OpenD integration</p></div><span className="badge">Local only</span></div><div className="empty"><LockKeyhole size={28}/><h3>Open the local dashboard to connect Moomoo</h3><p>{data?.notice || 'Sign into Moomoo OpenD locally, test quote access, then enable five-minute US data collection. Moomoo prices and gateway diagnostics are not part of public research uploads.'}</p></div></section>;
  if (!data || !draft) return <section className="panel padded"><h2>Loading US quote monitor</h2><p role="status">{error || 'Reading saved configuration and connection checks…'}</p></section>;
  return <>
    <div className="info-strip"><PlugZap size={21}/><span><b>US intraday research · Moomoo quote API.</b> This optional local feed keeps regular, pre-market, after-hours and overnight observations labelled. Completed five-minute bars appear only after a successful data-access test.</span></div>
    <div className="intraday-status-grid">
      <section className="panel padded"><small>CONNECTION CHECK</small><h2 className={good ? 'green-text' : 'amber-text'}>{last ? good ? 'Sample validated' : last.status.replaceAll('_', ' ') : 'Not tested'}</h2><p className="muted">{last ? localTime(last.checked_at) : 'Your API entitlements have not been checked'}</p></section>
      <section className="panel padded"><small>AUTOMATIC COLLECTION</small><h2>{data.config.enabled ? 'Enabled · every 5 min' : 'Paused'}</h2><p className="muted">{data.config.symbols.length} selected US instruments · saved revision {data.config.revision}</p></section>
      <section className="panel padded"><small>SELECTED DATA</small><h2>{data.freshness}</h2><p className="muted">Last completed bar: {localTime(data.last_bar_end)}</p></section>
    </div>
    <section className="panel"><div className="panel-head"><div><h2>Connect and verify</h2><p>Quote-only adapter · no account lookup or order interface</p></div><span className={`badge ${good ? 'green' : 'amber'}`}>{good ? 'Actual sample' : 'Access unverified'}</span></div><div className="padded">
      <p className="muted">Install <a href="https://www.moomoo.com/download/OpenAPI" target="_blank" rel="noreferrer">Moomoo OpenD</a>, which is separate from Moomoo desktop, and sign in directly there. Keep OpenD listening locally at {data.gateway}. The SDK is {data.sdk_installed ? 'installed' : 'not installed; run pip install -r backend/moomoo-requirements.txt'}.</p>
      <div className="action-row intraday-actions"><label>Inspect US instrument<select aria-label="US intraday instrument" disabled={blocked} value={symbol || data.symbol} onChange={e => { setSymbol(e.target.value); setError(''); }}>{data.instruments.map((i: Data) => <option value={i.symbol} key={i.symbol}>{i.symbol} · {i.name}</option>)}</select></label><button className="button" disabled={blocked || !data.sdk_installed || !data.requests_enabled} onClick={() => void action('probe')}><PlugZap size={15}/>Test Moomoo connection</button><button className="button secondary" disabled={blocked || !data.config.enabled} onClick={() => void action('refresh')}><RefreshCw size={15}/>Refresh US bars</button></div>
      {!data.requests_enabled && <p className="amber-text">Actual quote requests are disabled in fixture mode. Start MarketLens with DATA_MODE=yahoo to test Moomoo.</p>}{last && <div className={`intraday-result ${good ? 'green-text' : 'amber-text'}`} role="status"><b>{last.message}</b><p>The test validates returned bars for the requested instrument(s). It does not establish complete US exchange volume or guaranteed all-session coverage.</p></div>}
      {error && <p role="alert" className="amber-text">{error}</p>}{message && <p role="status" className="muted">{message}</p>}
    </div></section>
    <section className="panel"><div className="panel-head"><div><h2>{data.symbol} · 5-minute observations</h2><p>USD source prices · unadjusted provider OHLC · chart times in Melbourne</p></div><span className="badge">{data.mode === 'actual' ? 'Actual cached data' : 'Unavailable'}</span></div>
      {bars.length ? <><IntradayCandles bars={bars}/><div className="padded"><p className="muted">Source range: {localTime(bars[0].start_at)} to {localTime(bars[bars.length - 1].end_at)} · {bars.length} observed candles · source times also retained in US Eastern. Missing intervals are not filled.</p><div className="intraday-session-counts">{['pre-market', 'regular', 'after-hours', 'overnight'].map(s => <span className="badge" key={s}>{s} · {bars.filter(b => b.session === s).length} bars</span>)}</div></div></> : <div className="empty"><Activity size={28}/><h3>No verified Moomoo bars yet</h3><p>OpenD must be running and signed in with US API quote permissions. Press Test Moomoo connection to check. Yahoo daily data and synthetic fixtures are never shown here as Moomoo prices.</p></div>}
    </section>
    <section className="panel"><div className="panel-head"><div><h2>US collection settings</h2><p>Activation requires a fresh successful quote test for every selected symbol</p></div>{dirty && <span className="badge amber">Unsaved draft</span>}</div><form className="padded" onSubmit={e => { e.preventDefault(); void action('save'); }}>
      <label className="check-label"><input type="checkbox" aria-label="Collect US bars automatically" disabled={blocked} checked={draft.enabled} onChange={e => { setDraft({ ...draft, enabled: e.target.checked }); setDirty(true); }}/>Collect completed US bars every five minutes</label>
      <div className="intraday-symbols">{data.instruments.map((i: Data) => <label className="check-label" key={i.symbol}><input type="checkbox" aria-label={`Collect ${i.symbol}`} disabled={blocked || draft.symbols.length >= 20 && !draft.symbols.includes(i.symbol)} checked={draft.symbols.includes(i.symbol)} onChange={e => { setDraft({ ...draft, symbols: e.target.checked ? [...draft.symbols, i.symbol] : draft.symbols.filter((s: string) => s !== i.symbol) }); setDirty(true); }}/>{i.symbol}</label>)}</div>
      <button type="submit" className="button" disabled={blocked || !dirty || !draft.symbols.length}><Save size={15}/>Save collection settings</button><p className="muted">Select up to 20 included US instruments. Add more through Settings → Market universe. A failed activation leaves the saved configuration unchanged.</p>
    </form></section>
    <section className="panel padded"><h2>Research boundaries</h2><p className="muted">Intraday monitoring has separate storage and does not create trading signals, change daily setup scores or feed the current EOD backtests/paper account. API permissions and promotional free access depend on your account. Full consolidated volume, venue coverage and permission to redistribute data remain unverified.</p><p className="muted">Moomoo observations stay local. Public GitHub Pages continues showing its existing daily research uploads. The current US session is {data.current_session}; observed source times and cached-bar freshness are displayed separately.</p><a href="https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html" target="_blank" rel="noreferrer">Moomoo session and subscription documentation</a></section>
    {last?.symbols?.length > 0 && <section className="panel"><div className="panel-head"><h2>Latest quote validation</h2></div><div className="table-scroll"><table><thead><tr><th>Instrument</th><th>Result</th><th>Accepted</th><th>Unsettled skipped</th><th>Rejected</th></tr></thead><tbody>{last.symbols.map((s: Data) => <tr key={s.symbol}><td>{s.symbol}</td><td>{s.status}</td><td>{num(s.accepted, 0)}</td><td>{s.incomplete}</td><td>{Object.entries(s.rejected).map(([k,v]) => <small key={k}>{String(v)} · {k}</small>)}</td></tr>)}</tbody></table></div></section>}
  </>;
}
