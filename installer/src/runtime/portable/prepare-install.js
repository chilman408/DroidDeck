// SPDX-License-Identifier: GPL-3.0-only
// Unprivileged preparation only; no system targets, Android or service operations.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
const candidate = require('./runtime-candidate');
const dependencies = require('./dependency-bundle');
const artifacts = require('./artifact-build');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const FRONTEND_PIN = 'fbca122a28633e3e9fb5c80653297dfed1c997bd17fe59b07f01b11691834f7d';

function privatePath(filename, missing = false) {
  const home = path.resolve(os.homedir());
  if (typeof filename !== 'string' || !path.isAbsolute(filename) || path.resolve(filename) !== filename ||
      !filename.startsWith(home + path.sep)) throw Error('Canonical private home path required');
  let current = path.parse(filename).root;
  for (const part of filename.slice(current.length).split(path.sep)) {
    current = path.join(current, part);
    let info;
    try { info = fs.lstatSync(current); }
    catch (error) { if (missing && current === filename && error.code === 'ENOENT') return; throw error; }
    if (!info.isDirectory() || info.isSymbolicLink() || info.mode & 0o022 ||
        ![0, process.getuid()].includes(info.uid)) throw Error('Private original preparation namespace required');
  }
}

function writeNew(filename, raw) {
  const fd = fs.openSync(filename, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  try { fs.writeFileSync(fd, raw); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}

function pinnedNpmMetadata(raw, expected) {
  // The independently accepted npm receipts bind the original CRLF metadata.
  // Runtime Git blobs are LF. Render that one exact recipe, then check its pin.
  const text = new TextDecoder('utf-8', { fatal: true }).decode(raw);
  const bytes = Buffer.from(text.replace(/\r\n/g, '\n').replace(/\n/g, '\r\n'));
  if (sha(bytes) !== expected) throw Error('Original locked npm metadata required');
  return bytes;
}

function defaults() {
  return { candidate: candidate.buildRuntimeCandidate,
    frontend: args => execFileSync('/usr/bin/node', [path.join(__dirname, 'frontend-bundle.js'), ...args], {
      env: { PATH: '/usr/bin:/bin', LANG: 'C', LC_ALL: 'C', GIT_CONFIG_NOSYSTEM: '1',
        GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0' },
      timeout: 60000, maxBuffer: 1024 * 1024, stdio: 'pipe' }),
    dependency: dependencies.buildDependencyBundle, reproduce: artifacts.reproduceArtifacts,
    npm: (directory, environment) => execFileSync('/usr/bin/npm', ['ci', '--ignore-scripts', '--omit=dev',
      '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org'], {
        cwd: directory, env: environment, timeout: 180000, maxBuffer: 1024 * 1024, stdio: 'pipe' }) };
}

function prepare(acquired, output, { packageRoot = path.resolve(__dirname, '../..'), operations = defaults() } = {}) {
  if (process.platform !== 'linux' || !process.getuid || process.getuid() < 1000 || process.arch !== 'x64') {
    throw Error('Unprivileged Linux x86_64 preparation required');
  }
  privatePath(acquired); privatePath(output, true);
  const inputsRoot = path.join(packageRoot, 'build/installation-inputs');
  // Independent candidate and recipe validators remain the final authorities.
  const inputs = JSON.parse(readBoundedRegular(path.join(acquired, 'inputs.json'), 16384));
  const expected = { libwsRepo: 'libws.git', apiRepo: 'api.git', gbinderRepo: 'gbinder.git', glibutilRepo: 'glibutil.git',
    d8Jar: 'r8.jar', androidJar: 'android.jar', scrcpyServer: 'scrcpy-server-v4.0' };
  if (Object.keys(inputs).sort().join(',') !== Object.keys(expected).sort().join(',') ||
      Object.entries(expected).some(([key, value]) => inputs[key] !== path.join(acquired, value))) {
    throw Error('Fixed newly acquired publisher inputs required');
  }
  const recipeManifest = readBoundedRegular(path.join(inputsRoot, 'recipes/bundle-manifest.json'), 65536);
  const recipe = JSON.parse(recipeManifest);
  if (recipe.kind !== 'owned-install-recipe-source' || recipe.files?.length !== 15) throw Error('Packaged owned recipe sources required');
  for (const row of recipe.files) {
    if (!/^runtime\/(?:portable|browser-connection|waydroid-display)\/[A-Za-z0-9-]+\.(js|py)$/.test(row.path)) {
      throw Error('Closed packaged owned recipe source path required');
    }
    const raw = readBoundedRegular(path.join(inputsRoot, 'recipes', row.path), 1024 * 1024);
    if (raw.length !== row.size || sha(raw) !== row.sha256) throw Error('Packaged owned recipe bytes differ');
  }
  fs.mkdirSync(output, { mode: 0o700 });
  const identity = fs.lstatSync(output, { bigint: true });
  function check() {
    privatePath(output);
    const current = fs.lstatSync(output, { bigint: true });
    if (identity.dev !== current.dev || identity.ino !== current.ino || current.mode !== identity.mode) {
      throw Error('Original preparation workspace required');
    }
  }
  writeNew(path.join(output, 'intent.json'), Buffer.from(JSON.stringify({ version: 1, kind: 'installation-preparation',
    acquired, candidateManifestSha256: candidate.MANIFEST_SHA256, frontendManifestSha256: FRONTEND_PIN,
    servicesActivated: false }) + '\n'));
  const runtime = path.join(output, 'runtime');
  console.error('Preparing pinned runtime source');
  operations.candidate({ base: path.join(inputsRoot, 'base'), guard: path.join(inputsRoot, 'guard'), output: runtime });
  check();
  const bundles = {};
  for (const [role, directory] of Object.entries({ gateway: 'preview-gateway', display: 'waydroid-display' })) {
    console.error('Preparing locked ' + role + ' dependencies');
    const stage = path.join(output, role + '-npm');
    fs.mkdirSync(stage, { mode: 0o700 });
    for (const [name, pin] of [['package.json', dependencies.PINS[role].manifestSha256],
      ['package-lock.json', dependencies.PINS[role].lockSha256]]) {
      const raw = readBoundedRegular(path.join(runtime, directory, name), 1024 * 1024);
      writeNew(path.join(stage, name), pinnedNpmMetadata(raw, pin));
    }
    operations.npm(stage, { PATH: '/usr/bin:/bin', HOME: os.homedir(), LANG: 'C', LC_ALL: 'C',
      npm_config_userconfig: '/dev/null', npm_config_globalconfig: path.join(output, 'absent-global-npmrc'),
      npm_config_cache: path.join(output, 'npm-cache'), npm_config_ignore_scripts: 'true',
      npm_config_audit: 'false', npm_config_fund: 'false', npm_config_update_notifier: 'false' });
    bundles[role] = path.join(output, role + '-dependencies');
    operations.dependency({ role, source: stage, output: bundles[role] });
    check();
  }
  const browser = path.join(output, 'frontend');
  console.error('Preparing pinned browser frontend');
  operations.frontend(['build', path.join(acquired, 'frontend.git'), browser]);
  if (sha(readBoundedRegular(path.join(browser, 'bundle-manifest.json'), 65536)) !== FRONTEND_PIN) {
    throw Error('Independent accepted browser manifest required');
  }
  check();
  const pair = path.join(output, 'artifact-pair');
  console.error('Building and comparing two complete artifact sets');
  const comparison = operations.reproduce(inputs, pair);
  if (comparison.freshBuildInvocationsCompleted !== 2 || comparison.artifactBytesMatched !== true ||
      comparison.buildReceiptDigestsMatched !== true) throw Error('Two complete matching artifact builds required');
  check();
  const result = { version: 1, kind: 'prepared-installation-inputs', runtime, ...bundles, frontend: browser,
    recipes: path.join(inputsRoot, 'recipes'), artifacts: path.join(pair, 'first/artifacts'),
    auroraApk: path.join(acquired, 'AuroraStore-4.8.4.apk'), comparison,
    installable: false, servicesActivated: false, androidOperationsExecuted: false };
  writeNew(path.join(output, 'ready.json'), Buffer.from(JSON.stringify(result, null, 2) + '\n'));
  const fd = fs.openSync(output, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  check();
  return result;
}

function main(args) {
  if (args.length !== 2) throw Error('ACQUIRED_INPUTS NEW_PREPARATION required');
  console.log(JSON.stringify(prepare(...args)));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) {
  console.error('Installation preparation incomplete; preserve new output; no system services changed'); process.exitCode = 1;
} }
module.exports = { prepare, privatePath, pinnedNpmMetadata, defaults, main };
