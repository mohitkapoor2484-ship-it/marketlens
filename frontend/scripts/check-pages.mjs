import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve('dist-pages');
const html = readFileSync(resolve(root, 'index.html'), 'utf8');
const references = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(m => m[1]);
assert(references.some(path => path.endsWith('.js')), 'Built HTML must load JavaScript');
for (const path of references) {
  if (path.startsWith('https:')) continue;
  assert(path.startsWith('/marketlens/'), `Unexpected root asset URL: ${path}`);
  assert(existsSync(resolve(root, path.slice('/marketlens/'.length))), `Missing built asset: ${path}`);
}
assert(existsSync(resolve(root, '.nojekyll')));
const json = path => JSON.parse(readFileSync(resolve(root, 'pages-data', path), 'utf8'));
const snapshot = json('snapshot.json');
assert.equal(snapshot.preview.kind, 'synthetic_static_fixture');
assert.equal(snapshot.overview.scan.mode, 'demo');
assert.equal(snapshot.overview.paper.milestone.generated, 0);
assert.equal(snapshot.overview.paper.milestone.evaluable, 0);
assert.equal(snapshot.settings.environment.key_configured, false);
assert.equal(snapshot.settings.environment.scheduler_enabled, false);
for (const item of snapshot.settings.universe) {
  const stock = json(`stocks/${item.symbol}.json`);
  assert.equal(stock.mode, 'demo');
  assert.equal(stock.news.length, 0);
  assert(stock.bars.length > 0);
}
for (const test of snapshot.backtests) {
  assert.equal(json(`backtests/${test.id}.json`).manifest.mode, 'demo');
  assert(existsSync(resolve(root, 'pages-data/backtests', `${test.id}.csv`)));
}
const replay = json(`paper/${snapshot.replay_account}.json`);
assert.equal(replay.kind, 'replay');
assert.equal(replay.audit_valid, true);
assert.equal(replay.milestone.generated, 0);
console.log(`Pages artifact verified: project-prefixed assets, ${snapshot.settings.universe.length} fixture charts, saved experiment/replay, zero actual forward signals.`);
