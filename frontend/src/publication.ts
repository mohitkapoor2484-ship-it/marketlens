/** Atomic, checksum-verified public research reader. No GitHub credentials. */
type Json = Record<string, any>;
const schema = 'marketlens-research-v1';

export class PublishedResearch {
  repository: string;
  branch: string;
  fetcher: typeof fetch;
  data?: Json;
  checkedAt = 0;
  pending?: Promise<Json>;

  constructor(repository: string, branch = 'research-data', fetcher: typeof fetch = fetch) {
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) || !/^[A-Za-z0-9_-]+$/.test(branch)) throw new Error('Invalid public research repository');
    this.repository = repository;
    this.branch = branch;
    // Native browser fetch needs Window as its receiver, not this client object.
    this.fetcher = fetcher.bind(globalThis);
  }

  async load(force = false): Promise<Json> {
    if (this.pending) return this.pending;
    if (!force && this.data && Date.now() - this.checkedAt < 50_000) return this.data;
    this.pending = this.read().catch(error => {
      if (!this.data) throw error;
      // Preserve the last verified observation, with a visible transport warning.
      this.data.overview.publication.error = error instanceof Error ? error.message : 'Upload check failed';
      return this.data;
    }).finally(() => { this.pending = undefined; this.checkedAt = Date.now(); });
    return this.pending;
  }

  private async get(path: string): Promise<Response> {
    const response = await this.fetcher(`https://raw.githubusercontent.com/${this.repository}/${path}`, { cache: 'no-store', signal: AbortSignal.timeout(15_000) });
    if (!response.ok) throw new Error(`Published research unavailable (HTTP ${response.status}). Keep the local publisher running and check its upload status.`);
    return response;
  }

  private async read(): Promise<Json> {
    const response = await this.get(`${this.branch}/latest.json?check=${Date.now()}`);
    const manifest = await response.json();
    if (manifest.schema !== schema || manifest.kind !== 'real_research_snapshot' || !/^[a-f0-9]{40}$/.test(manifest.data_commit) || !/^[a-f0-9]{64}$/.test(manifest.content_hash) || !Number.isFinite(Date.parse(manifest.published_at)) || Date.parse(manifest.published_at) > Date.now() + 300_000) throw new Error('Invalid published research manifest');
    if (this.data && Date.parse(manifest.published_at) < Date.parse(this.data.overview.publication.published_at)) throw new Error('An older cached upload was received; the newer verified snapshot is retained');
    let data = this.data;
    if (!data || data.overview.publication.content_hash !== manifest.content_hash) {
      const body = await (await this.get(`${manifest.data_commit}/data.json`)).text();
      const bytes = new TextEncoder().encode(body);
      if (bytes.length > 8_000_000) throw new Error('Published research exceeds the allowed size');
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(x => x.toString(16).padStart(2, '0')).join('');
      if (hash !== manifest.content_hash) throw new Error('Published research checksum mismatch; last verified snapshot retained');
      const parsed: Json = JSON.parse(body);
      if (parsed?.schema !== schema || parsed?.kind !== 'real_research_snapshot' || !['yahoo', 'eodhd'].includes(parsed?.overview?.scan?.mode) || !parsed.overview.paper?.private || !Array.isArray(parsed.overview.scan.candidates) || parsed.overview.scan.candidates.some((c: Json) => c.mode !== parsed.overview.scan.mode) || !parsed.stocks || !parsed.settings) throw new Error('Published data is not a valid actual-research snapshot');
      data = parsed;
    }
    const loaded = data as Json;
    loaded.overview.publication = { ...manifest, repository: this.repository, branch: this.branch };
    this.data = loaded;
    return loaded;
  }
}
