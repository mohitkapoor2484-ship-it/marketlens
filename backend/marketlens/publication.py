"""Allowlisted, read-only research snapshots. No account or secret publication.

The data branch is independent of main. An atomic manifest points to an immutable
data commit, so uploads do not require a Pages rebuild or expose a home API.
"""
import base64
import copy
import hashlib
import json
import os
import re
import subprocess

import httpx

from .db import rules
from .domain import canonical, stamp
from .providers import PROVIDER_INFO, ProviderError
from .strategies import STRATEGIES

SCHEMA = "marketlens-research-v1"
NEWS_FIELDS = ("title", "url", "source", "published_at", "fetched_at", "feed_provider")
BAR_FIELDS = ("date", "open", "high", "low", "close", "volume", "sma20", "sma50", "sma200", "ema20", "rsi", "macd", "macd_signal", "macd_hist", "atr", "atr_pct", "volume_ratio", "volatility")
CANDIDATE_FIELDS = ("symbol", "name", "market", "currency", "score", "score_components", "weights", "setups", "qualified", "exclusions", "relative_strength", "market_cap", "explanation", "rule_version", "asof", "fetched_at", "dataset_id", "benchmark_dataset_id", "mode", "data_status", "news_status", "volume_basis")


def select(value, keys):
    return {key: copy.deepcopy(value[key]) for key in keys if key in value}


def public_news(items):
    return [select(item, NEWS_FIELDS) for item in items[:10]]


def build_snapshot(service):
    if service.settings.data_mode not in {"yahoo", "eodhd"}:
        raise ValueError("Only explicitly selected actual-data research can be published; fixture publishing uses its separate exporter")
    scan = service.latest_scan()
    if not scan.get("created_at") or not scan.get("candidates"):
        raise ValueError("Run an actual-data research scan before uploading")
    if hasattr(service, "universe_revision") and scan.get("universe_revision") != service.universe_revision:
        raise ValueError("Universe changed; rescan cached research before uploading")
    candidates = []
    for original in scan["candidates"]:
        if original.get("mode") != service.settings.data_mode:
            raise ValueError("Mixed dataset modes cannot be published")
        item = select(original, CANDIDATE_FIELDS)
        item["indicators"] = select(original["indicators"], BAR_FIELDS)
        item["plan"] = select(original.get("plan") or {}, ("hypothetical", "type", "entry", "stop", "target", "target_1", "reward_risk", "invalidation", "entry_condition"))
        item["news"] = public_news(original.get("news", []))
        candidates.append(item)
    shortlist_symbols = {item["symbol"] for item in scan["shortlist"]}
    public_scan = select(scan, ("created_at", "mode", "scan_id", "rule_version", "notice", "universe_revision"))
    public_scan.update(candidates=candidates, shortlist=[c for c in candidates if c["symbol"] in shortlist_symbols],
                       unavailable=[select(x, ("symbol", "reason")) for x in scan.get("unavailable", [])])
    health = service.data_health()
    public_health = select(health, ("mode", "provider_lag_minutes", "intraday"))
    public_health["provider"] = PROVIDER_INFO[service.settings.data_mode]
    public_health["symbols"] = [select(row, ("symbol", "market", "last_session", "expected_session", "fetched_at", "status", "missing_sessions", "news_status", "dataset_id")) for row in health["symbols"]]
    public_health["fx"] = select(health["fx"], ("last_date", "fetched_at")) if health.get("fx") else None
    public_health.update(jobs=[], scheduler_enabled=False, hosting="Published research snapshot; engine runs on the owner's local computer")
    stocks = {}
    for instrument in service.universe:
        try:
            source = service.stock(instrument.symbol)
        except ValueError:
            continue  # Truly unavailable symbols stay unavailable, never fixture-filled.
        if source.get("mode") != service.settings.data_mode:
            raise ValueError("Mixed stock dataset modes cannot be published")
        stock = select(source, ("dataset_id", "mode", "fetched_at", "news_status", "volume_basis", "source_provider", "source_symbol", "price_basis", "news_provider"))
        stock["instrument"] = select(source["instrument"], ("symbol", "name", "market", "benchmark", "role"))
        stock["bars"] = [select(bar, BAR_FIELDS) for bar in source["bars"][-300:]]
        stock["actions"] = [select(a, ("kind", "date", "ratio", "amount", "payment_date", "currency")) for a in source["actions"]]
        stock["news"] = public_news(source["news"])
        stocks[instrument.symbol] = stock
    # A private marker is deliberate: no fake portfolio balances, counts or results.
    private_paper = {"private": True, "kind": "local_only"}
    with service.db.session() as session:
        configuration = rules(session).model_dump()
    settings = {"rules": {"scanner": configuration["scanner"], "strategy": configuration["strategy"]},
                "universe": [select(i.model_dump(), ("symbol", "name", "market", "benchmark", "role")) for i in service.universe],
                "strategies": STRATEGIES, "provider": PROVIDER_INFO[service.settings.data_mode],
                "environment": {"mode": service.settings.data_mode, "hosting": "read-only published research"},
                "notice": "Only selected market research is published. Portfolio, orders, ledger, reports, saved backtests and environment settings remain local."}
    from .discovery import discovery_view
    # Explicit discovery allowlist: no universe obligation reasons or account state.
    discovery = {"provider": {}, "markets": [], "mode": service.settings.data_mode}
    if hasattr(service, "universe_revision"):
        source = discovery_view(service)
        discovery = select(source, ("provider", "mode", "enabled", "presets", "maximum_selection"))
        discovery["markets"] = []
        for market in source["markets"]:
            item = select(market, ("market", "run_id", "created_at", "request", "version", "mode", "status", "groups", "errors", "last_attempt"))
            item["rows"] = [select(row, ("symbol", "source_symbol", "name", "market", "kind", "exchange", "currency", "quote_price", "volume", "average_volume_3m", "approximate_turnover", "relative_volume_3m", "change_percent", "above_sma50", "above_sma200", "source_at", "fetched_at", "quote_status", "in_universe", "reason")) for row in market["rows"]]
            discovery["markets"].append(item)
    return {"schema": SCHEMA, "kind": "real_research_snapshot", "overview": {"scan": public_scan, "health": public_health, "paper": private_paper}, "discovery": discovery,
            "settings": settings, "stocks": stocks, "backtests": [], "reports": []}


def read_state(settings):
    try:
        return json.loads(settings.publish_state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(settings, value):
    path = settings.publish_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(canonical(value), encoding="utf-8")
    temporary.replace(path)


def publication_status(service):
    cfg = service.settings
    return {**select(read_state(cfg), ("last_attempt_at", "published_at", "content_hash", "data_commit", "manifest_commit", "status", "error")),
            "enabled": cfg.publish_enabled, "interval_minutes": cfg.publish_interval_minutes,
            "repository": cfg.publish_repository, "branch": cfg.publish_branch,
            "scope": "Market research only; no accounts, orders, ledger, keys or saved backtests"}


def github_credential(settings):
    token = settings.publish_github_token.get_secret_value()
    if token:
        return token
    owner = settings.publish_repository.split("/")[0]
    try:
        response = subprocess.run(["git", "-c", "credential.interactive=never", "credential", "fill"],
                                  input=f"protocol=https\nhost=github.com\nusername={owner}\n\n", text=True,
                                  capture_output=True, timeout=15,
                                  env={**os.environ, "GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"},
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if response.returncode == 0:
            token = dict(line.split("=", 1) for line in response.stdout.splitlines() if "=" in line).get("password")
            if token:
                return token
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise ProviderError("GitHub publishing authentication unavailable. Sign in through Git Credential Manager or set PUBLISH_GITHUB_TOKEN locally with repository Contents write permission")


class GitHubDataStore:
    def __init__(self, settings):
        self.repository, self.branch = settings.publish_repository, settings.publish_branch
        self.interval = settings.publish_interval_minutes
        if self.branch.lower() in {"main", "master", "gh-pages"}:
            raise ValueError("Use a dedicated research-data branch; publishing cannot modify a code or Pages branch")
        self.client = httpx.Client(base_url=f"https://api.github.com/repos/{self.repository}/", timeout=30,
                                   headers={"Authorization": f"Bearer {github_credential(settings)}", "Accept": "application/vnd.github+json",
                                            "X-GitHub-Api-Version": "2022-11-28"})

    def close(self):
        self.client.close()

    def request(self, method, path, body=None, missing=False):
        try:
            result = self.client.request(method, path or str(self.client.base_url).rstrip("/"), json=body)
        except httpx.HTTPError:
            raise ProviderError("GitHub upload connection failed; previous published snapshot is unchanged") from None
        if missing and result.status_code == 404:
            return None
        if not 200 <= result.status_code < 300:
            raise ProviderError(f"GitHub upload HTTP {result.status_code}; check credentials, permissions, rate limits or a conflicting publisher")
        try:
            return result.json()
        except ValueError:
            raise ProviderError("GitHub returned an invalid upload response") from None

    def blob(self, value):
        return self.request("POST", "git/blobs", {"content": base64.b64encode(value).decode("ascii"), "encoding": "base64"})["sha"]

    def commit_file(self, name, value, parent, message):
        blob = self.blob(value)
        tree = self.request("POST", "git/trees", {"tree": [{"path": name, "mode": "100644", "type": "blob", "sha": blob}]})["sha"]
        return self.request("POST", "git/commits", {"message": message, "tree": tree, "parents": [parent] if parent else []})["sha"]

    def publish(self, content, content_hash, prior, published_at, scan_created_at):
        repository = self.request("GET", "")
        if repository.get("private") or repository.get("full_name", "").lower() != self.repository.lower():
            raise ValueError("This static publisher requires the explicitly configured public repository")
        if self.branch == repository.get("default_branch"):
            raise ValueError("Publishing to the default code branch is forbidden")
        ref = self.request("GET", f"git/ref/heads/{self.branch}", missing=True)
        parent = ref["object"]["sha"] if ref else None
        if parent:
            existing = self.request("GET", f"contents/latest.json?ref={parent}", missing=True)
            try:
                manifest = json.loads(base64.b64decode(existing["content"]))
                if manifest["schema"] != SCHEMA or manifest["kind"] != "real_research_snapshot":
                    raise ValueError()
            except (TypeError, KeyError, ValueError):
                raise ValueError("The data branch contains unrecognised content; choose an unused dedicated publishing branch") from None
            prior = {**manifest, "manifest_commit": parent}
        # Only trust cached data when our previous manifest is still branch HEAD.
        reusable = prior.get("content_hash") == content_hash and prior.get("manifest_commit") == parent and re.fullmatch(r"[a-f0-9]{40}", prior.get("data_commit", ""))
        data_commit = prior["data_commit"] if reusable else self.commit_file("data.json", content, parent, "Update allowlisted actual research snapshot")
        manifest = {"schema": SCHEMA, "kind": "real_research_snapshot", "content_hash": content_hash, "data_commit": data_commit,
                    "published_at": published_at, "scan_created_at": scan_created_at, "upload_interval_minutes": self.interval,
                    "notice": "Delayed daily observations. Upload time is not a new price timestamp. Engine and private account remain local."}
        # data.json's commit is immutable and reachable as an ancestor of manifest.
        commit = self.commit_file("latest.json", canonical(manifest).encode(), parent if reusable else data_commit, "Publish research heartbeat")
        if ref:
            self.request("PATCH", f"git/refs/heads/{self.branch}", {"sha": commit, "force": False})
        else:
            self.request("POST", "git/refs", {"ref": f"refs/heads/{self.branch}", "sha": commit})
        return {**manifest, "manifest_commit": commit}


def publish_research(service, store_factory=GitHubDataStore):
    cfg = service.settings
    if not cfg.publish_enabled:
        return {"status": "disabled", "enabled": False}
    previous = read_state(cfg)
    attempted = stamp()
    store = None
    try:
        payload = build_snapshot(service)
        content = canonical(payload).encode("utf-8")
        if len(content) > 8_000_000:
            raise ValueError("Research snapshot exceeds 8 MB; reduce the published universe")
        content_hash = hashlib.sha256(content).hexdigest()
        store = store_factory(cfg)
        result = store.publish(content, content_hash, previous, attempted, payload["overview"]["scan"]["created_at"])
        state = {**result, "status": "success", "last_attempt_at": attempted}
        save_state(cfg, state)
        return publication_status(service)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, ProviderError)) else "Research upload failed; inspect the local job status"
        save_state(cfg, {**previous, "status": "failed", "last_attempt_at": attempted, "error": message[:500]})
        raise ProviderError(message) from None
    finally:
        if store:
            store.close()


def upload_tick(service):
    if not service.operations.acquire(blocking=False):
        return {"status": "busy", "notice": "Research operation in progress; next scheduled upload will retry"}
    try:
        return service.job("publication:upload", lambda: publish_research(service))
    finally:
        service.operations.release()
