// SPDX-License-Identifier: GPL-3.0-only
// Generate only the explicitly selected public source inputs, never Git history.
const fs = require('node:fs'), path = require('node:path'), { execFileSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const { buildRuntimeBundle } = require('./runtime-bundle');
const { BASE_MANIFEST, GUARD_REVISION, OVERLAYS } = require('./runtime-candidate');
const sha = value => createHash('sha256').update(value).digest('hex');
function prepareInputs(repo, output) {
  fs.mkdirSync(output, { mode: 0o700 });
  const base = path.join(output, 'base'), guard = path.join(output, 'guard');
  buildRuntimeBundle({ repo, output: base });
  if (sha(fs.readFileSync(path.join(base, 'bundle-manifest.json'))) !== BASE_MANIFEST) throw Error('Base source recipe differs');
  fs.mkdirSync(guard, { mode: 0o700 });
  for (const [file, pin] of Object.entries(OVERLAYS)) {
    const bytes = execFileSync('git', ['--no-replace-objects', '--no-lazy-fetch', '-C', path.resolve(repo),
      'show', GUARD_REVISION + ':runtime/' + file], { maxBuffer: 1024 * 1024, timeout: 30000, windowsHide: true,
      env: { ...process.env, GIT_NO_LAZY_FETCH: '1', GIT_ALLOW_PROTOCOL: '' } });
    if (bytes.length !== pin.size || sha(bytes) !== pin.sha256) throw Error('Guard source recipe differs');
    const filename = path.join(guard, file); fs.mkdirSync(path.dirname(filename), { mode: 0o700, recursive: true });
    fs.writeFileSync(filename, bytes, { flag: 'wx', mode: 0o600 });
  }
  return { base, guard };
}
if (require.main === module) {
  try {
    if (process.argv.length !== 5 || process.argv[2] !== '--prepare') throw Error('Fixed input command required');
    prepareInputs(process.argv[3], process.argv[4]); console.log('Pinned public-source inputs prepared; no installation performed');
  } catch (_) { console.error('Source input preparation refused; preserve any new output'); process.exitCode = 1; }
}
module.exports = { prepareInputs };
