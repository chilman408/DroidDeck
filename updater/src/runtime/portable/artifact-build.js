// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), crypto = require('node:crypto');
const { readBoundedRegular } = require('./read-bounded');
const ROLES = Object.freeze(['touch', 'api', 'display', 'gps', 'logical', 'binder']);
const COMPONENTS = Object.freeze({ touch: 'touch-native-preview', api: 'api-native-preview',
  display: 'diagnostic-display-native-preview', gps: 'gps-java-dex-preview',
  logical: 'logical-capture-java-dex-preview', binder: 'binder-shared-library-preview' });
const SOURCES = Object.freeze({ touch: ['tesla-android-x86-touch'], api: ['tesla-android-x86-api'],
  display: ['tesla-android-x86-display-latest'], gps: ['location-bridge.jar'], logical: ['logical-capture.jar'],
  binder: ['libs/libglibutil.so.1.0.80', 'libs/libgbinder.so.1.1.45'] });
const INPUTS = Object.freeze(['libwsRepo', 'apiRepo', 'gbinderRepo', 'glibutilRepo', 'd8Jar', 'androidJar', 'scrcpyServer']);
const PUBLISHER_PIN = Object.freeze({ size: 732226,
  sha256: '84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a' });
const PUBLISHER = 'scrcpy-server-v4.0', MANIFEST = 'bundle-manifest.json', LIMIT = 8 * 1024 * 1024;
const FILES = Object.freeze([...Object.values(SOURCES).flat().map(p => path.posix.basename(p)), PUBLISHER].sort());
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function keys(value, expected) {
  if (!value || Array.isArray(value) || typeof value !== 'object' ||
      Object.keys(value).sort().join(',') !== [...expected].sort().join(',')) throw Error('Invalid artifact metadata');
}
function absolute(value) {
  if (typeof value !== 'string' || value.length > 4096 || value.includes('\0') ||
      !path.isAbsolute(value) || path.resolve(value) !== value) throw Error('Canonical absolute path required');
  return value;
}
function directoryChain(value, privateHome) {
  const filename = absolute(value); let current = path.parse(filename).root;
  for (const segment of filename.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment); const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw Error('Unsafe build directory');
    if (privateHome && (current === privateHome || current.startsWith(privateHome + path.sep)) &&
        (stat.uid !== process.getuid() || stat.mode & 0o022)) throw Error('Non-private build directory');
  }
}
function readArtifact(filename) { directoryChain(path.dirname(filename)); return readBoundedRegular(filename, LIMIT); }
function recordMatches(record, bytes, filename) {
  if (!record || (record.path ?? record.name) !== filename || record.size !== bytes.length ||
      !bytes.length || record.sha256 !== sha(bytes)) throw Error('Artifact differs from build evidence');
}
function writeNew(filename, bytes) {
  const fd = fs.openSync(filename, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL, 0o600);
  try { fs.writeFileSync(fd, bytes); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
function validateReceipt(role, receipt) {
  if (!receipt || receipt.format !== 1 || receipt.component !== COMPONENTS[role] || receipt.installable !== false) {
    throw Error('Unrecognized builder result');
  }
  let valid;
  if (['touch', 'api', 'display'].includes(role)) {
    valid = receipt.nativeTestsPassed === true && receipt.elfStaticX86_64 === true;
    if (role !== 'touch') valid = valid && receipt.serverExecuted === false;
    if (role === 'display') valid = valid && receipt.androidOperationsExecuted === false && receipt.browserPngFallback === false;
  } else if (role === 'binder') valid = receipt.symbolSmokePassed === true && receipt.binderOperationsExecuted === false;
  else valid = receipt.hostTestsPassed === true && receipt.dexReparsePassed === true && receipt.androidOperationsExecuted === false;
  if (!valid) throw Error('Required component tests did not pass');
}
function validateManifest(m, publisherPin) {
  keys(m, ['version', 'product', 'kind', 'installable', 'servicesActivated', 'androidOperationsExecuted', 'files', 'buildReceipts']);
  if (m.version !== 1 || m.product !== 'DroidDeck' || m.kind !== 'runtime-artifact-build-component' ||
      m.installable !== false || m.servicesActivated !== false || m.androidOperationsExecuted !== false ||
      !Array.isArray(m.files) || m.files.length !== FILES.length ||
      !Array.isArray(m.buildReceipts) || m.buildReceipts.length !== ROLES.length) throw Error('Invalid artifact component');
  m.files.forEach((f, i) => {
    keys(f, ['path', 'size', 'sha256']);
    if (f.path !== FILES[i] || !Number.isSafeInteger(f.size) || f.size < 1 || f.size > LIMIT ||
        typeof f.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(f.sha256)) throw Error('Invalid artifact record');
    if (f.path === PUBLISHER && (f.size !== publisherPin.size || f.sha256 !== publisherPin.sha256)) throw Error('Publisher artifact mismatch');
  });
  m.buildReceipts.forEach((r, i) => {
    keys(r, ['component', 'sha256']);
    if (r.component !== COMPONENTS[ROLES[i]] || typeof r.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(r.sha256)) {
      throw Error('Invalid build evidence record');
    }
  });
  return m;
}
function _verifyArtifacts(directory, publisherPin = PUBLISHER_PIN) {
  const root = absolute(directory); directoryChain(root);
  if (fs.readdirSync(root).sort().join('\n') !== [...FILES, MANIFEST].sort().join('\n')) throw Error('Unexpected artifact inventory');
  const m = validateManifest(JSON.parse(readBoundedRegular(path.join(root, MANIFEST), 65536)), publisherPin);
  for (const f of m.files) recordMatches(f, readArtifact(path.join(root, f.path)), f.path);
  return m;
}
function defaultBuilders() {
  return { touch: require('./touch-build').buildTouch, api: require('./api-build').buildApi,
    display: require('./display-build').buildDisplay, gps: require('./gps-build').buildGps,
    logical: require('./logical-capture-build').buildLogicalCapture, binder: require('./binder-build').buildBinder };
}
function _buildArtifacts(inputs, output, { builders = defaultBuilders(), publisherPin = PUBLISHER_PIN } = {}) {
  // Injection is for generated fixtures only. The CLI never accepts tools or pin overrides.
  keys(inputs, INPUTS); for (const name of INPUTS) absolute(inputs[name]);
  const root = absolute(output); directoryChain(path.dirname(root));
  const publisher = readArtifact(inputs.scrcpyServer);
  recordMatches({ path: PUBLISHER, ...publisherPin }, publisher, PUBLISHER);
  fs.mkdirSync(root, { mode: 0o700 });
  const work = path.join(root, 'builds'); fs.mkdirSync(work, { mode: 0o700 });
  const selected = [], buildReceipts = [];
  for (const role of ROLES) {
    directoryChain(root);
    const destination = path.join(work, role), options = { output: destination };
    if (['touch', 'display'].includes(role)) options.libwsRepo = inputs.libwsRepo;
    else if (role === 'api') options.upstreamRepo = inputs.apiRepo;
    else if (role === 'binder') Object.assign(options, { gbinderRepo: inputs.gbinderRepo, glibutilRepo: inputs.glibutilRepo });
    else { options.d8Jar = inputs.d8Jar; if (role === 'logical') options.androidJar = inputs.androidJar; }
    const receipt = builders[role](options); validateReceipt(role, receipt);
    const records = role === 'binder' ? [receipt.artifacts?.glibutil, receipt.artifacts?.gbinder] :
      ['gps', 'logical'].includes(role) ? [receipt.artifacts?.jar] : [receipt.artifact];
    SOURCES[role].forEach((source, i) => {
      const bytes = readArtifact(path.join(destination, source)); recordMatches(records[i], bytes, source);
      selected.push({ path: path.posix.basename(source), bytes });
    });
    buildReceipts.push({ component: COMPONENTS[role], sha256: sha(Buffer.from(JSON.stringify(receipt))) });
  }
  // Only the closed payload is distributable. Build logs and input paths stay outside it.
  directoryChain(root); const artifacts = path.join(root, 'artifacts'); fs.mkdirSync(artifacts, { mode: 0o700 });
  selected.push({ path: PUBLISHER, bytes: publisher }); selected.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  const files = selected.map(f => {
    writeNew(path.join(artifacts, f.path), f.bytes); return { path: f.path, size: f.bytes.length, sha256: sha(f.bytes) };
  });
  const manifest = validateManifest({ version: 1, product: 'DroidDeck', kind: 'runtime-artifact-build-component',
    installable: false, servicesActivated: false, androidOperationsExecuted: false, files, buildReceipts }, publisherPin);
  const pending = path.join(artifacts, MANIFEST + '.partial');
  writeNew(pending, JSON.stringify(manifest, null, 2) + '\n');
  // Publish only synced bytes, without overwriting a raced destination.
  fs.linkSync(pending, path.join(artifacts, MANIFEST)); fs.unlinkSync(pending);
  return _verifyArtifacts(artifacts, publisherPin);
}
function checkBuildOutput(output) {
  if (process.platform !== 'linux' || process.arch !== 'x64' || !process.getuid || process.getuid() === 0) {
    throw Error('Unprivileged Linux x86_64 required');
  }
  const home = path.resolve(os.homedir()); absolute(output);
  if (!output.startsWith(home + path.sep)) throw Error('Build output outside home');
  directoryChain(path.dirname(output), home);
}
function buildArtifacts(inputs, output) {
  checkBuildOutput(output);
  return _buildArtifacts(inputs, output);
}
function verifyArtifacts(directory) { return _verifyArtifacts(directory); }
function _compareArtifacts(left, right, publisherPin = PUBLISHER_PIN) {
  const roots = [absolute(left), absolute(right)];
  if (roots[0] === roots[1] || roots.some((root, i) => root.startsWith(roots[1 - i] + path.sep))) {
    throw Error('Disjoint artifact directories required');
  }
  const identities = roots.map(root => {
    directoryChain(root);
    const info = fs.lstatSync(root, { bigint: true });
    return { dev: info.dev, ino: info.ino };
  });
  if (identities[0].dev === identities[1].dev && identities[0].ino === identities[1].ino) {
    throw Error('Distinct artifact directory identities required');
  }
  const generation = manifest => JSON.stringify({
    files: manifest.files.map(f => ({ path: f.path, size: f.size, sha256: f.sha256 })),
    buildReceipts: manifest.buildReceipts.map(r => ({ component: r.component, sha256: r.sha256 }))
  });
  const observed = roots.map(root => generation(_verifyArtifacts(root, publisherPin)));
  if (observed[0] !== observed[1]) throw Error('Artifact reproducibility comparison mismatch');
  // Reverify bytes as well as metadata; this is an observation, not retained ownership.
  roots.forEach((root, i) => {
    if (generation(_verifyArtifacts(root, publisherPin)) !== observed[i]) throw Error('Artifact generation changed during comparison');
    const info = fs.lstatSync(root, { bigint: true });
    if (info.dev !== identities[i].dev || info.ino !== identities[i].ino) throw Error('Artifact directory changed during comparison');
  });
  return Object.freeze({ kind: 'runtime-artifact-reproducibility-observation', version: 1,
    artifactBytesMatched: true, buildReceiptDigestsMatched: true, payloadFiles: FILES.length,
    buildComponents: ROLES.length, comparisonSha256: sha(Buffer.from(observed[0])),
    independentBuildExecutionVerified: false, filesWritten: false, installable: false,
    servicesActivated: false, androidOperationsExecuted: false });
}
function compareArtifacts(left, right) { return _compareArtifacts(left, right); }
function _reproduceArtifacts(inputs, output, options) {
  keys(inputs, INPUTS); for (const name of INPUTS) absolute(inputs[name]);
  const root = absolute(output), publisherPin = options?.publisherPin ?? PUBLISHER_PIN;
  directoryChain(path.dirname(root));
  recordMatches({ path: PUBLISHER, ...publisherPin }, readArtifact(inputs.scrcpyServer), PUBLISHER);
  fs.mkdirSync(root, { mode: 0o700 });
  const held = [];
  function retain(directory) {
    directoryChain(directory);
    const info = fs.lstatSync(directory, { bigint: true });
    held.push({ directory, dev: info.dev, ino: info.ino, mode: info.mode });
  }
  function check(names) {
    for (const before of held) {
      directoryChain(before.directory);
      const info = fs.lstatSync(before.directory, { bigint: true });
      if (info.dev !== before.dev || info.ino !== before.ino || info.mode !== before.mode) {
        throw Error('Original fresh build directory required');
      }
    }
    if (fs.readdirSync(root).sort().join('\n') !== [...names].sort().join('\n')) throw Error('Closed build-pair inventory required');
  }
  retain(root);
  const names = [];
  for (const name of ['first', 'second']) {
    check(names);
    const destination = path.join(root, name);
    _buildArtifacts(inputs, destination, options);
    retain(destination); retain(path.join(destination, 'artifacts'));
    names.push(name); check(names);
  }
  const comparison = _compareArtifacts(path.join(root, 'first/artifacts'), path.join(root, 'second/artifacts'), publisherPin);
  check(names);
  return Object.freeze({ ...comparison, kind: 'fresh-runtime-artifact-build-pair',
    freshBuildInvocationsCompleted: 2, sameHostBuildComparisonVerified: true, filesWritten: true });
}
function reproduceArtifacts(inputs, output) {
  checkBuildOutput(output);
  return _reproduceArtifacts(inputs, output);
}
function main(args) {
  let result;
  if (args.length === 3 && args[0] === 'compare') {
    console.log(JSON.stringify(compareArtifacts(args[1], args[2]))); return;
  }
  if (args.length === 3 && args[0] === 'reproduce') {
    console.log(JSON.stringify(reproduceArtifacts(JSON.parse(readBoundedRegular(absolute(args[1]), 16384)), args[2]))); return;
  }
  if (args.length === 3 && args[0] === 'build') {
    result = buildArtifacts(JSON.parse(readBoundedRegular(absolute(args[1]), 16384)), args[2]);
  } else if (args.length === 2 && args[0] === 'verify') result = verifyArtifacts(args[1]);
  else throw Error('Use build/reproduce INPUTS_JSON NEW_OUTPUT, verify ARTIFACT_DIRECTORY or compare LEFT RIGHT');
  console.log(JSON.stringify({ kind: result.kind, files: result.files.length, installable: false,
    servicesActivated: false, androidOperationsExecuted: false }));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) {
  console.error('Artifact build rejected; partial output is not installable'); process.exitCode = 1;
} }
module.exports = { buildArtifacts, verifyArtifacts, compareArtifacts, reproduceArtifacts,
  _buildArtifacts, _verifyArtifacts, _compareArtifacts, _reproduceArtifacts, main };
