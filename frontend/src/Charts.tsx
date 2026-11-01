import { useEffect, useRef, useState } from 'react';
import { ColorType, createChart, CrosshairMode, LineStyle, Time } from 'lightweight-charts';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, AreaChart, Area } from 'recharts';
import { Data, money, localTime } from './api';

export function IntradayCandles({ bars }: { bars: Data[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const chart = createChart(ref.current, { height: 380, layout: { background: { type: ColorType.Solid, color: '#111821' }, textColor: '#8b99ac' }, grid: { vertLines: { color: '#1c2633' }, horzLines: { color: '#1c2633' } }, timeScale: { timeVisible: true, secondsVisible: false }, localization: { timeFormatter: (t: any) => localTime(new Date(Number(t) * 1000).toISOString()) } });
    const candles = chart.addCandlestickSeries({ upColor: '#5dd8b2', downColor: '#ec8698', borderVisible: false, wickUpColor: '#5dd8b2', wickDownColor: '#ec8698' });
    candles.setData(bars.map(b => ({ time: (Date.parse(b.start_at) / 1000) as Time, open: b.open, high: b.high, low: b.low, close: b.close })));
    candles.priceScale().applyOptions({ scaleMargins: { top: .08, bottom: .25 } });
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume' });
    volume.priceScale().applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
    volume.setData(bars.map(b => ({ time: (Date.parse(b.start_at) / 1000) as Time, value: b.volume, color: b.close >= b.open ? '#5dd8b23a' : '#ec86983a' })));
    chart.applyOptions({ timeScale: { tickMarkFormatter: (t: any) => new Date(Number(t) * 1000).toLocaleTimeString('en-AU', { timeZone: 'Australia/Melbourne', hour: '2-digit', minute: '2-digit' }) } });
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 100), to: bars.length + 3 });
    const ro = new ResizeObserver(entries => chart.applyOptions({ width: entries[0].contentRect.width }));
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, [bars]);
  return <><div className="chart-toolbar"><span className="tag">5 MIN · USD · UNADJUSTED</span><span className="muted">Melbourne time · completed observations only</span></div><div ref={ref} aria-label="US five-minute candlestick chart with volume in Melbourne time"/><a className="chart-credit" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></>;
}

export function Candles({ bars, currency }: { bars: Data[], currency: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [overlays, setOverlays] = useState(['sma20', 'sma50', 'sma200']);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const chart = createChart(ref.current, { height: 420, layout: { background: { type: ColorType.Solid, color: '#111821' }, textColor: '#8b99ac', fontFamily: 'Segoe UI, sans-serif' }, grid: { vertLines: { color: '#1c2633' }, horzLines: { color: '#1c2633' } }, rightPriceScale: { borderColor: '#273242' }, timeScale: { borderColor: '#273242' }, crosshair: { mode: CrosshairMode.Normal } });
    const candles = chart.addCandlestickSeries({ upColor: '#5dd8b2', downColor: '#ec8698', borderVisible: false, wickUpColor: '#5dd8b2', wickDownColor: '#ec8698', priceScaleId: 'right', priceFormat: {type:'custom', formatter: (p: number) => `${currency} ${p.toFixed(2)}`, minMove:.01} });
    candles.setData(bars.map(b => ({ time: b.date as Time, open: b.open, high: b.high, low: b.low, close: b.close })));
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.25 } });
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume' });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(bars.map(b => ({ time: b.date as Time, value: b.volume, color: b.close >= b.open ? '#5dd8b23a' : '#ec86983a' })));
    const colors: Data = { sma20: '#6baaf5', sma50: '#e6bf76', sma200: '#b09ee9', ema20: '#b8dc9c' };
    overlays.forEach(key => {
      const line = chart.addLineSeries({ color: colors[key], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, lineStyle: key.startsWith('ema') ? LineStyle.Dashed : LineStyle.Solid });
      line.setData(bars.filter(b => b[key] != null).map(b => ({ time: b.date as Time, value: b[key] })));
    });
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 100), to: bars.length + 3 });
    const ro = new ResizeObserver(entries => chart.applyOptions({ width: entries[0].contentRect.width }));
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, [bars, overlays, currency]);
  return <><div className="chart-toolbar"><span className="tag">1D · RAW OHLC</span>{['sma20', 'sma50', 'sma200', 'ema20'].map(k => <label key={k}><input type="checkbox" checked={overlays.includes(k)} onChange={() => setOverlays(overlays.includes(k) ? overlays.filter(x => x !== k) : [...overlays, k])}/>{k.toUpperCase()}</label>)}<span className="muted">Scroll to zoom · drag to pan</span></div><div ref={ref} aria-label="Interactive daily candlestick chart with volume and selected moving averages"/><a className="chart-credit" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></>;
}

export function EquityChart({ curve = [], benchmark = [], drawdown = false }: { curve: Data[], benchmark?: Data[], drawdown?: boolean }) {
  const bmap = new Map(benchmark.map(b => [b.date, b.equity]));
  const data = curve.map(r => ({ ...r, benchmark: bmap.get(r.date), drawdown: r.drawdown * 100 }));
  if (!data.length) return <div className="chart-empty"><span>No observations yet</span><p>Equity history appears after eligible sessions are evaluated.</p></div>;
  return <div className="equity-chart" role="img" aria-label={drawdown ? 'Drawdown over time' : 'AUD equity over time'}><ResponsiveContainer width="100%" height={240}>{drawdown ? <AreaChart data={data}><CartesianGrid stroke="#23303d" strokeDasharray="3 5" vertical={false}/><XAxis dataKey="date" tick={{ fill: '#8997a7', fontSize: 10 }} minTickGap={70}/><YAxis tickFormatter={x => `${x.toFixed(0)}%`} tick={{ fill: '#8997a7', fontSize: 11 }} width={58}/><Tooltip contentStyle={{ background: '#17202b', border: '1px solid #344254' }} formatter={(v: number) => `${v.toFixed(2)}%`}/><Area dataKey="drawdown" stroke="#ec8698" fill="#ec869825"/></AreaChart> : <LineChart data={data}><CartesianGrid stroke="#23303d" strokeDasharray="3 5" vertical={false}/><XAxis dataKey="date" tick={{ fill: '#8997a7', fontSize: 10 }} minTickGap={70}/><YAxis domain={['auto', 'auto']} tickFormatter={x => `${(x / 1000).toFixed(1)}k`} tick={{ fill: '#8997a7', fontSize: 11 }} width={58}/><Tooltip contentStyle={{ background: '#17202b', border: '1px solid #344254', borderRadius: 8 }} formatter={(v: number) => money(v)}/><Line name="Strategy equity · AUD" type="linear" dataKey="equity" stroke="#5dd8b2" dot={false} strokeWidth={2} isAnimationActive={false}/>{benchmark.length > 0 && <Line name="Market benchmark · AUD" dataKey="benchmark" stroke="#7e9bbc" dot={false} strokeDasharray="5 4" isAnimationActive={false}/>}</LineChart>}</ResponsiveContainer></div>;
}
