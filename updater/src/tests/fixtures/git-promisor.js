// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), cp = require('node:child_process');

// Generated fixtures or explicitly public source caches only; never a signed-in checkout.
function missingPublicObject({ source, revision, missingPath, root }) {
  const origin = path.join(root, 'public-origin'), repository = path.join(root, 'missing-object');
  const env = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: process.platform === 'win32' ? 'NUL' : '/dev/null',
    GIT_TERMINAL_PROMPT: '0', GIT_ALLOW_PROTOCOL: 'file' };
  function git(directory, args, input) {
    return cp.execFileSync('git', ['--no-pager', '--no-replace-objects', '-C', directory, ...args],
      { env, input, timeout: 10000, maxBuffer: 8 * 1024 * 1024, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  }
  git(root, ['clone', '--quiet', '--bare', '--no-hardlinks', source, origin]);
  git(origin, ['config', 'uploadpack.allowAnySHA1InWant', 'true']);
  fs.mkdirSync(repository, { mode: 0o700 }); git(repository, ['init', '--quiet', '--bare']);
  const absent = git(origin, ['rev-parse', revision + ':' + missingPath]).toString().trim();
  const objects = git(origin, ['rev-list', '--objects', '--all']).toString().trim().split('\n').map(line => line.split(' ')[0]);
  for (const oid of new Set(objects)) {
    if (oid === absent) continue;
    if (!/^[a-f0-9]{40}$/.test(oid)) throw new Error('Unexpected fixture object');
    const kind = git(origin, ['cat-file', '-t', oid]).toString().trim();
    if (!['commit', 'tree', 'blob', 'tag'].includes(kind)) throw new Error('Unexpected fixture type');
    const bytes = git(origin, ['cat-file', kind, oid]);
    if (git(repository, ['hash-object', '-t', kind, '-w', '--stdin'], bytes).toString().trim() !== oid) throw new Error('Fixture object changed');
  }
  for (const [key, value] of [['core.repositoryformatversion', '1'], ['extensions.partialClone', 'origin'],
    ['remote.origin.url', origin], ['remote.origin.promisor', 'true'], ['remote.origin.partialclonefilter', 'blob:none'], ['protocol.file.allow', 'always']]) git(repository, ['config', key, value]);
  const packNames = () => fs.readdirSync(path.join(repository, 'objects/pack')).sort();
  const hasObject = () => cp.spawnSync('git', ['--no-replace-objects', '-C', repository, 'cat-file', '-e', absent],
    { env: { ...env, GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' }, timeout: 10000, windowsHide: true }).status === 0;
  return { repository, hasObject, packNames, fetchHead: path.join(repository, 'FETCH_HEAD') };
}
module.exports = { missingPublicObject };
