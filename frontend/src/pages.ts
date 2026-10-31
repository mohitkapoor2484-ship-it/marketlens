/** Explicit static-fixture adapter. Never used as a fallback for a failed real API. */
import type { Data } from './api';

const root = `${import.meta.env.BASE_URL}pages-data/`;
let snapshotPromise: Promise<Data> | undefined;
const engineMessage = 'GitHub Pages serves this synthetic preview without a Python engine. Run scripts/start.ps1 locally for scans, new backtests, rule edits, scheduling and forward paper trading. See the GitHub Pages guide linked above.';

async function jsonFile(path: string): Promise<Data> {
  const response = await fetch(root + path);
  if (!response.ok) throw new Error(`Preview data could not be loaded (${response.status}). Refresh after the Pages deployment finishes.`);
  return response.json();
}
function snapshot() { return snapshotPromise ??= jsonFile('snapshot.json'); }
function watchlist(): Data[] {
  try {
    const value = JSON.parse(localStorage.getItem('marketlens:pages:watchlist:v1') || '[]');
    return Array.isArray(value) ? value.filter(w => typeof w?.symbol === 'string' && typeof w?.added_at === 'string') : [];
  } catch { return []; }
}
export function previewDownloadUrl(path: string) {
  const url = new URL(path, 'https://preview.invalid');
  if (url.pathname.startsWith('/backtests/') && url.pathname.endsWith('/export')) {
    const id = url.pathname.split('/')[2];
    return `${root}backtests/${encodeURIComponent(id)}.${url.searchParams.get('format') === 'csv' ? 'csv' : 'json'}`;
  }
  if (url.pathname === '/paper/export') return `${root}paper/${encodeURIComponent(url.searchParams.get('account') || 'forward')}.json`;
  if (url.pathname.startsWith('/reports/')) return `${root}reports/${encodeURIComponent(url.pathname.split('/').at(-1)!)}`;
  throw new Error('This download requires the running Python research service.');
}

export async function pagesApi(path: string, method: string, body?: unknown): Promise<any> {
  const data = await snapshot();
  const url = new URL(path, 'https://preview.invalid');
  const route = url.pathname;
  if (route === '/watchlist') {
    if (method === 'GET') return watchlist();
    if (method === 'POST') {
      const symbol = (body as Data)?.symbol;
      if (!data.settings.universe.some((i: Data) => i.symbol === symbol)) throw new Error('Choose an instrument from the fixture universe.');
      const list = watchlist();
      if (!list.some(w => w.symbol === symbol)) list.push({ symbol, added_at: new Date().toISOString() });
      localStorage.setItem('marketlens:pages:watchlist:v1', JSON.stringify(list));
      return { saved: true, persistence: 'browser-only' };
    }
  }
  if (route.startsWith('/watchlist/') && method === 'DELETE') {
    localStorage.setItem('marketlens:pages:watchlist:v1', JSON.stringify(watchlist().filter(w => w.symbol !== decodeURIComponent(route.split('/')[2]))));
    return { removed: true, persistence: 'browser-only' };
  }
  if (method !== 'GET') throw new Error(engineMessage);
  if (route === '/overview') return structuredClone(data.overview);
  if (route === '/settings') return structuredClone(data.settings);
  if (route === '/scanner') return structuredClone(data.overview.scan);
  if (route === '/data/health') return structuredClone(data.overview.health);
  if (route === '/backtests') return structuredClone(data.backtests);
  if (route === '/reports') return data.reports.map((r: Data) => ({ ...r, url: root + r.url }));
  if (route === '/paper') return jsonFile(`paper/${encodeURIComponent(url.searchParams.get('account') || 'forward')}.json`);
  if (route.startsWith('/stocks/')) return jsonFile(`stocks/${encodeURIComponent(decodeURIComponent(route.split('/')[2]))}.json`);
  if (route.startsWith('/backtests/')) return jsonFile(`backtests/${encodeURIComponent(route.split('/')[2])}.json`);
  if (route === '/news') return { articles: [], mode: 'demo', sources: [] };
  throw new Error(engineMessage);
}
