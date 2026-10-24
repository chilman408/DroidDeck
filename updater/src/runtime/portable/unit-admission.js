// SPDX-License-Identifier: GPL-3.0-only
const path = require('node:path'), { createHash } = require('node:crypto'), { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { normalizeRuntimeConfig, deriveRuntimeLayout, readRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const { verifyInstallProfile, checkProfileAccount } = require('./install-profile');
const run = promisify(execFile), sha = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const COMMANDS = ['ExecCondition', 'ExecStartPre', 'ExecStart', 'ExecStartPost', 'ExecReload', 'ExecReloadPost', 'ExecStop', 'ExecStopPost'];
const DEPENDENCIES = ['Requires', 'Wants', 'After'];
function expectedUnits(input, profileVersion = 2) {
  if (![2, 3].includes(profileVersion)) throw Error('Supported headless machine profile version required');
  const config = normalizeRuntimeConfig(input), layout = deriveRuntimeLayout(config), prefix = '/etc/systemd/system/';
  const worker = layout.root + '/runtime/portable/worker-entry.js';
  const command = args => sha([[args[0], args, []]]), empty = sha([]);
  const paths = ['NODE_PATH=' + layout.root + '/runtime/preview-gateway/node_modules:' + layout.root + '/runtime/waydroid-display/node_modules',
    'LD_LIBRARY_PATH=' + layout.hostLibraries, 'DROIDDECK_CONFIGURATION_SHA256=' + configurationDigest(config)];
  const desktop = ['HOME=' + config.home, 'XDG_RUNTIME_DIR=' + layout.runtimeDirectory,
    'DBUS_SESSION_BUS_ADDRESS=' + layout.dbusAddress, 'WAYLAND_DISPLAY=wayland-teslaandroid', ...paths];
  const record = (name, user, group, env, start, pre) => ({ LoadState: 'loaded', NeedDaemonReload: false, Transient: false,
    FragmentPath: prefix + name, DropInPaths: [], Type: 'simple', NotifyAccess: 'none', User: user, Group: group,
    SupplementaryGroups: [], EnvironmentSha256: sha([...env].sort()), EnvironmentFilesSha256: empty,
    PassEnvironment: [], UnsetEnvironment: [], WorkingDirectory: '',
    ...Object.fromEntries(COMMANDS.map(k => [k + 'Sha256', empty])), ExecStartSha256: command(start),
    ExecStartPreSha256: pre ? command(['/usr/bin/node', worker, pre]) : empty,
    KillMode: 'control-group', KillSignal: 15, Restart: 'on-failure', TimeoutStopUSec: 20000000,
    TimeoutStartUSec: 90000000, Requires: [], Wants: [], After: [], PartOf: [] });
  const cName = 'tesla-waydroid-compositor.service', hName = 'tesla-waydroid-headless-session.service', userManager = 'user@' + config.uid + '.service';
  const compositor = record(cName, config.user, String(config.gid), desktop,
    ['/usr/bin/weston', '--backend=pipewire', '--renderer=gl', '--socket=wayland-teslaandroid', '--width=1280', '--height=720',
      '--idle-time=0', '--no-config', '--shell=kiosk-shell.so'], 'compositor-check');
  Object.assign(compositor, { SupplementaryGroups: ['render', 'video'], Requires: [userManager], After: [userManager] });
  const headless = record(hName, config.user, String(config.gid), desktop,
    ['/usr/bin/python3', '-I', '-B', layout.root + (profileVersion === 3 ?
      '/headless-owned-runtime/headless-owned-runtime.py' : '/headless-runtime/headless-runtime.py')], 'headless-check');
  Object.assign(headless, { Type: 'notify', NotifyAccess: 'main', KillSignal: 10, TimeoutStartUSec: 60000000, TimeoutStopUSec: 45000000,
    SupplementaryGroups: ['render', 'video'], DropInPaths: [prefix + hName + '.d/60-droiddeck-headless.conf'],
    Requires: [userManager, cName], Wants: ['waydroid-container.service'], After: [userManager, cName, 'waydroid-container.service'],
    PartOf: ['waydroid-container.service', cName] });
  const runtime = record('tesla-waydroid-runtime.service', 'root', '', [...paths, 'TESLA_WAYDROID_CAPTURE_BACKEND=compositor'],
    ['/usr/bin/node', worker, 'runtime']);
  Object.assign(runtime, { WorkingDirectory: layout.root + '/runtime/waydroid-display', TimeoutStopUSec: 900000000,
    Wants: ['network-online.target', 'waydroid-container.service', hName, 'droiddeck-browser-connection.service'],
    After: ['network-online.target', 'waydroid-container.service', hName, 'droiddeck-browser-connection.service'] });
  const browser = record('droiddeck-browser-connection.service', 'root', String(config.gid), paths, ['/usr/bin/node', worker, 'connection']);
  browser.After = ['local-fs.target', 'systemd-tmpfiles-setup.service'];
  const container = record('waydroid-container.service', '', '',
    ['DROIDDECK_CONFIGURATION_SHA256=' + configurationDigest(config)], ['/usr/bin/waydroid', 'container', 'start']);
  Object.assign(container, { Type: 'dbus', Restart: 'no', TimeoutStopUSec: 90000000,
    ExecStartPreSha256: command(['/usr/bin/node', layout.root + '/pid-compat/portable/pid-startup.js']),
    FragmentPath: '/usr/lib/systemd/system/waydroid-container.service',
    DropInPaths: [prefix + 'waydroid-container.service.d/40-droiddeck-pid-compat.conf'] });
  return { [cName]: compositor, [hName]: headless, 'tesla-waydroid-runtime.service': runtime,
    'droiddeck-browser-connection.service': browser, 'waydroid-container.service': container };
}
function exactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== Object.keys(expected).sort().join(',')) throw Error('Invalid effective unit schema');
}
function normalizeSnapshot(input, expected) {
  exactKeys(input, expected); const result = {};
  for (const name of Object.keys(expected).sort()) {
    const record = input[name]; exactKeys(record, expected[name]); const out = {};
    for (const key of Object.keys(expected[name]).sort()) {
      const value = record[key], sample = expected[name][key];
      if (Array.isArray(sample)) {
        if (!Array.isArray(value) || value.length > 256 || value.some(v => typeof v !== 'string' || v.length > 4096 || v.includes('\0')) ||
            new Set(value).size !== value.length) throw Error('Invalid effective unit vector');
        out[key] = [...value].sort();
      } else {
        if (typeof value !== typeof sample || (typeof value === 'string' && (value.length > 4096 || value.includes('\0'))) ||
            (typeof value === 'number' && (!Number.isSafeInteger(value) || value < 0)) ||
            (key.endsWith('Sha256') && !/^[a-f0-9]{64}$/.test(value))) throw Error('Invalid effective unit field');
        out[key] = value;
      }
    }
    result[name] = out;
  }
  return result;
}
function compareUnits(configuration, input, profileVersion = 2) {
  const expected = expectedUnits(configuration, profileVersion), snapshot = normalizeSnapshot(input, expected), differences = [];
  for (const name of Object.keys(expected).sort()) {
    const fields = [];
    for (const [key, value] of Object.entries(expected[name])) {
      const actual = snapshot[name][key];
      if (DEPENDENCIES.includes(key) ? value.some(v => !actual.includes(v)) :
          JSON.stringify(actual) !== JSON.stringify(Array.isArray(value) ? [...value].sort() : value)) fields.push(key);
    }
    if (fields.length) differences.push({ unit: name, fields: fields.sort() });
  }
  return differences;
}
async function readSnapshot() {
  if (process.platform !== 'linux' || !process.getuid || process.getuid() === 0) throw Error('Unprivileged Linux unit inspection required');
  const result = await run('/usr/bin/python3', ['-I', '-B', path.join(__dirname, 'effective-units.py'), '--snapshot'], {
    timeout: 45000, maxBuffer: 131072, env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' }
  });
  if (result.stderr.length || !result.stdout.length) throw Error('Unit snapshot rejected');
  return JSON.parse(result.stdout);
}
async function inspectProfileUnits(directory, { snapshot = readSnapshot, lookup } = {}) {
  const before = verifyInstallProfile(directory);
  if (![2, 3].includes(before.version)) throw Error('Headless machine profile required');
  const config = readRuntimeConfig(path.join(directory, 'runtime.json'));
  if (configurationDigest(config) !== before.configurationDigest) throw Error('Profile generation changed');
  const first = await snapshot(), mismatches = compareUnits(config, first, before.version);
  const account = await checkProfileAccount(directory, lookup ? { lookup } : undefined);
  const second = await snapshot(), more = compareUnits(config, second, before.version);
  const expected = expectedUnits(config, before.version);
  const stable = JSON.stringify(normalizeSnapshot(first, expected)) === JSON.stringify(normalizeSnapshot(second, expected));
  if (JSON.stringify(before) !== JSON.stringify(verifyInstallProfile(directory))) throw Error('Profile changed during unit inspection');
  const merged = new Map();
  for (const { unit, fields } of [...mismatches, ...more]) merged.set(unit, [...new Set([...(merged.get(unit) || []), ...fields])].sort());
  return { kind: 'effective-base-session-units', configurationDigest: before.configurationDigest,
    accountValidated: account.accountValidated, baseSessionUnitsMatched: stable && merged.size === 0, snapshotStable: stable,
    mismatches: [...merged].map(([unit, fields]) => ({ unit, fields })), gpuRecoveryVerified: false,
    installable: false, readyForActivation: false, servicesActivated: false, androidOperationsExecuted: false,
    ...(before.version === 3 ? { profileVersion: 3 } : {}) };
}
async function main(args) {
  if (args.length !== 2 || args[0] !== 'inspect' || !path.isAbsolute(args[1]) || path.resolve(args[1]) !== args[1]) throw Error('Use inspect ABSOLUTE_PROFILE');
  const result = await inspectProfileUnits(args[1]); console.log(JSON.stringify(result));
  if (!result.baseSessionUnitsMatched) process.exitCode = 2;
}
if (require.main === module) main(process.argv.slice(2)).catch(() => {
  console.error('Effective unit admission rejected; no activation performed'); process.exitCode = 1;
});
module.exports = { compareUnits, inspectProfileUnits, readSnapshot, main };
