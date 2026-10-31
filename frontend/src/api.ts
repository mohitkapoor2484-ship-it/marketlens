import { pagesApi, previewDownloadUrl } from './pages';
export type Data = Record<string, any>;
export const isPagesPreview = import.meta.env.MODE === 'pages';
export const downloadUrl = (path: string) => isPagesPreview ? previewDownloadUrl(path) : `/api${path}`;
export async function api<T = Data>(path: string, method = 'GET', body?: unknown): Promise<T> {
  if (isPagesPreview) return pagesApi(path, method, body);
  const response = await fetch(`/api${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('The Python research API is unavailable. Start scripts/start.ps1 locally. GitHub Pages requires the dedicated Pages preview build.');
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
export const num = (n: number | null | undefined, digits = 2) => n == null || !Number.isFinite(n) ? '—' : new Intl.NumberFormat('en-AU', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n);
export const money = (n: number | null | undefined) => n == null ? '—' : `A$${num(n)}`;
export const pct = (n: number | null | undefined) => n == null ? '—' : `${num(n * 100)}%`;
export const localTime = (value?: string) => value ? new Date(value).toLocaleString('en-AU', { timeZone: 'Australia/Melbourne', dateStyle: 'medium', timeStyle: 'short' }) : 'Not observed';
export const label = (s: string) => s.replaceAll('_', ' ').replace(/^\w/, c => c.toUpperCase());
