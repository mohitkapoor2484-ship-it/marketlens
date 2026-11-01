import { useEffect, useState } from 'react';
import { Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { api, Data } from './api';

export function canonicalTicker(text: string, market: string) {
  let value = text.trim().toUpperCase().replace(/\.AX$/, '.AU');
  const suffix = market === 'ASX' ? '.AU' : '.US';
  if (/\.(AU|US)$/.test(value) && !value.endsWith(suffix)) throw new Error('Ticker suffix and selected market disagree.');
  if (!value.endsWith(suffix)) value += suffix;
  if (!/^[A-Z0-9][A-Z0-9.\-]{0,19}\.(AU|US)$/.test(value)) throw new Error('Enter an exchange ticker, for example BHP, VAS.AX, TSLA or BRK.B.');
  return value;
}

export function UniverseEditor({ items: publishedItems, readOnly = false, disabled = false, onBusy, onSaved }: {
  items: Data[], readOnly?: boolean, disabled?: boolean, onBusy?: (message: string) => void, onSaved?: () => Promise<void>
}) {
  const [items, setItems] = useState<Data[]>(publishedItems);
  const [revision, setRevision] = useState<number>();
  const [protectedItems, setProtected] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [query, setQuery] = useState('');
  const [form, setForm] = useState({ ticker: '', name: '', market: 'ASX', role: 'stock', benchmark: 'STW.AU' });
  const blocked = disabled || pending;
  const shown = readOnly ? publishedItems : items;
  const benchmarks = shown.filter(i => i.role === 'benchmark');

  async function reload() {
    setError(''); setPending(true);
    try {
      const saved = await api('/universe');
      setItems(saved.items); setRevision(saved.revision); setProtected(saved.protected); setDirty(false); setMessage('');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load the saved universe'); }
    finally { setPending(false); }
  }
  useEffect(() => { if (!readOnly) void reload(); }, [readOnly]);

  function edit(symbol: string, field: string, value: string) {
    setItems(old => old.map(i => i.symbol === symbol ? { ...i, [field]: value } : i)); setDirty(true); setMessage('');
  }
  function add() {
    setError(''); setMessage('');
    try {
      const symbol = canonicalTicker(form.ticker, form.market);
      if (items.some(i => i.symbol === symbol)) throw new Error(`${symbol} is already in this list.`);
      if (items.length >= 500) throw new Error('The local universe is limited to 500 instruments.');
      setItems(old => [...old, { symbol, name: form.name.trim() || symbol, market: form.market, role: form.role,
                                benchmark: form.role === 'benchmark' ? symbol : form.benchmark }]);
      setDirty(true); setForm({ ...form, ticker: '', name: '' });
    } catch (e) { setError(e instanceof Error ? e.message : 'Invalid instrument'); }
  }
  async function save() {
    setPending(true); setError(''); setMessage(''); onBusy?.('Saving the market universe');
    try {
      if (items.some(i => !i.name.trim())) throw new Error('Every instrument needs a non-blank display name.');
      const saved = await api('/universe', 'PUT', { items, expected_revision: revision });
      setItems(saved.items); setRevision(saved.revision); setProtected(saved.protected); setDirty(false);
      setMessage(saved.warning || `Saved revision ${saved.revision}. Changes are active now. Press Refresh & upload now to download new instruments; the next successful upload updates the public list.`);
      await onSaved?.();
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save universe'); }
    finally { setPending(false); onBusy?.(''); }
  }

  return <section className="panel universe-editor">
    <div className="panel-head"><div><h2>Market universe</h2><p>{shown.length} instruments · {shown.filter(i => i.role !== 'benchmark').length} stocks/ETFs to scan · {readOnly ? 'Published list · read-only' : `Saved revision ${revision ?? 'loading…'}`}</p></div>{dirty && <span className="badge amber">Unsaved changes</span>}</div>
    <div className="padded">
      <p className="muted">{readOnly ? 'Edit this list in Settings → Market universe on the computer running MarketLens. The next successful upload publishes the updated list.' : 'Add or remove individual instruments and save without restarting. Provider availability is checked when you refresh research. Adding an ETF enables the same EOD setup rules; it does not add the earlier ETF contribution-timing strategy.'}</p>
      {!readOnly && <form className="universe-add" onSubmit={e => { e.preventDefault(); add(); }}>
        <label>Market<select aria-label="New instrument market" value={form.market} disabled={blocked} onChange={e => setForm({ ...form, market: e.target.value, benchmark: e.target.value === 'ASX' ? 'STW.AU' : 'SPY.US' })}><option>ASX</option><option>US</option></select></label>
        <label>Type<select aria-label="New instrument type" value={form.role} disabled={blocked} onChange={e => setForm({ ...form, role: e.target.value })}><option value="stock">Stock</option><option value="etf">ETF</option><option value="benchmark">Benchmark only</option></select></label>
        <label>Ticker<input aria-label="New instrument ticker" required maxLength={23} placeholder={form.market === 'ASX' ? 'BHP or VAS.AX' : 'TSLA or BRK.B'} value={form.ticker} disabled={blocked} onChange={e => setForm({ ...form, ticker: e.target.value })}/></label>
        <label>Name (optional)<input aria-label="New instrument name" maxLength={150} placeholder="Display name" value={form.name} disabled={blocked} onChange={e => setForm({ ...form, name: e.target.value })}/></label>
        {form.role !== 'benchmark' && <label>Benchmark<select aria-label="New instrument benchmark" value={form.benchmark} disabled={blocked} onChange={e => setForm({ ...form, benchmark: e.target.value })}>{benchmarks.filter(i => i.market === form.market).map(i => <option key={i.symbol}>{i.symbol}</option>)}</select></label>}
        <button className="button secondary" type="submit" disabled={blocked || !revision}><Plus size={14}/>Add instrument</button>
      </form>}
      <label className="universe-search">Find an instrument<input aria-label="Find universe instrument" placeholder="Ticker or name" value={query} onChange={e => setQuery(e.target.value)}/></label>
      {error && <p className="amber-text" role="alert">{error}</p>}
      {message && <p className="green-text" role="status">{message}</p>}
    </div>
    <div className="table-scroll"><table><thead><tr><th>Instrument</th><th>Market</th><th>Type</th><th>Name</th><th>Benchmark</th>{!readOnly && <th>Remove</th>}</tr></thead><tbody>{shown.filter(i => `${i.symbol} ${i.name}`.toLowerCase().includes(query.toLowerCase())).map(item => {
      const inUse = item.role === 'benchmark' && shown.some(i => i.symbol !== item.symbol && i.benchmark === item.symbol);
      const reason = protectedItems[item.symbol] || (inUse ? 'Referenced by another instrument' : '');
      return <tr key={item.symbol}><td><b>{item.symbol}</b>{!readOnly && reason && <small className="muted">{reason}</small>}</td><td>{item.market}</td><td>{readOnly || item.role === 'benchmark' ? item.role.toUpperCase() : <select aria-label={`Type for ${item.symbol}`} value={item.role} disabled={blocked} onChange={e => edit(item.symbol, 'role', e.target.value)}><option value="stock">Stock</option><option value="etf">ETF</option></select>}</td><td>{readOnly ? item.name : <input aria-label={`Name for ${item.symbol}`} value={item.name} required maxLength={150} disabled={blocked} onChange={e => edit(item.symbol, 'name', e.target.value)}/>}</td><td>{readOnly || item.role === 'benchmark' ? item.benchmark : <select aria-label={`Benchmark for ${item.symbol}`} value={item.benchmark} disabled={blocked} onChange={e => edit(item.symbol, 'benchmark', e.target.value)}>{benchmarks.filter(b => b.market === item.market).map(b => <option key={b.symbol}>{b.symbol}</option>)}</select>}</td>{!readOnly && <td><button className="icon-button" title={reason || `Remove ${item.symbol} from future scans`} aria-label={`Remove ${item.symbol}`} disabled={blocked || !!reason} onClick={() => { setItems(old => old.filter(i => i.symbol !== item.symbol)); setDirty(true); setMessage(''); }}><Trash2 size={16}/></button></td>}</tr>;
    })}</tbody></table></div>
    {!readOnly && <div className="padded"><div className="action-row"><button className="button" onClick={() => void save()} disabled={blocked || !dirty || !revision}><Save size={14}/>Save universe</button><button className="button secondary" onClick={() => void reload()} disabled={blocked}><RefreshCw size={14}/>Reload saved list</button></div><p className="muted">STW and SPY are retained for the current portfolio benchmark. Active paper obligations protect their instruments from removal. Saving creates an auditable revision; existing signals, data and saved backtests are preserved. Unsaved changes stay in this editor until saved or reloaded.</p></div>}
  </section>;
}
