export type FundamentalFilters = { minCap: string, maxCap: string, minRoe: string, maxRoe: string, rating: string };
export const emptyFundamentalFilters: FundamentalFilters = { minCap: '', maxCap: '', minRoe: '', maxRoe: '', rating: 'any' };
export const analystLabels: Record<string, string> = { strong_buy: 'Strong Buy', buy: 'Buy', hold: 'Hold', underperform: 'Underperform', sell: 'Sell', strong_sell: 'Strong Sell' };
export const ratingOptions: [string, string][] = [['any','Any / include unrated'],['rated','Rated only'],['strong_buy','Strong Buy'],['buy_or_better','Buy or Strong Buy'],['hold_or_better','Hold or better'],['underperform_or_sell','Underperform / Sell']];
const allowed: Record<string, string[]> = { rated: Object.keys(analystLabels), strong_buy: ['strong_buy'], buy_or_better: ['strong_buy','buy'], hold_or_better: ['strong_buy','buy','hold'], underperform_or_sell: ['underperform','sell','strong_sell'] };
const value = (s: string) => s.trim() === '' ? null : Number(s);

export function fundamentalRequest(f: FundamentalFilters) {
  return { min_market_cap: value(f.minCap) == null ? null : Number(f.minCap) * 1e6,
           max_market_cap: value(f.maxCap) == null ? null : Number(f.maxCap) * 1e6,
           min_roe_percent: value(f.minRoe), max_roe_percent: value(f.maxRoe), analyst_rating: f.rating };
}
export function fundamentalFilterError(f: FundamentalFilters) {
  const cap = [value(f.minCap), value(f.maxCap)], roe = [value(f.minRoe), value(f.maxRoe)];
  if (cap.some(n => n != null && (!Number.isFinite(n) || n < 0 || n > 1e10))) return 'Market cap must be a non-negative number in millions.';
  if (roe.some(n => n != null && (!Number.isFinite(n) || Math.abs(n) > 100000))) return 'Enter a finite ROE percentage between -100,000 and 100,000.';
  if ([cap, roe].some(([a,b]) => a != null && b != null && a > b)) return 'Minimum cannot exceed maximum for market cap or ROE.';
  return '';
}
export function matchesFundamentals(row: Record<string, any>, f: FundamentalFilters) {
  if (fundamentalFilterError(f)) return false;
  const p = fundamentalRequest(f);
  for (const [field, lo, hi] of [['market_cap', p.min_market_cap, p.max_market_cap], ['roe_percent', p.min_roe_percent, p.max_roe_percent]] as const) {
    if (lo == null && hi == null) continue;
    const n = row[field];
    if (typeof n !== 'number' || !Number.isFinite(n) || lo != null && n < lo || hi != null && n > hi) return false;
  }
  return f.rating === 'any' || (allowed[f.rating] || []).includes(row.analyst_rating);
}
export function compareDiscovery(a: Record<string, any>, b: Record<string, any>, key: string) {
  const fallback = key === 'analyst_mean' ? Infinity : -Infinity;
  const x = typeof a[key] === 'number' && Number.isFinite(a[key]) ? a[key] : fallback;
  const y = typeof b[key] === 'number' && Number.isFinite(b[key]) ? b[key] : fallback;
  return (key === 'analyst_mean' ? x - y : y - x) || a.symbol.localeCompare(b.symbol);
}
