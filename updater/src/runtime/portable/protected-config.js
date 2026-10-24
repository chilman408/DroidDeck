// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs');
const { readRuntimeConfig, normalizeRuntimeConfig } = require('./runtime-config');
const CONFIG_FILE = '/etc/droiddeck/runtime.json';
function protectedStat(stat, directory) {
  if (stat.uid !== 0 || stat.mode & 0o022 || !(directory ? stat.isDirectory() : stat.isFile()) ||
      (!directory && (stat.nlink !== 1 || stat.size > 16384))) throw new Error('Unsafe runtime configuration metadata');
}
function readProtectedRuntimeConfig({ io = fs, read = readRuntimeConfig } = {}) {
  try {
    for (const directory of ['/', '/etc', '/etc/droiddeck']) protectedStat(io.lstatSync(directory), true);
    const before = io.lstatSync(CONFIG_FILE); protectedStat(before, false);
    const config = normalizeRuntimeConfig(read(CONFIG_FILE));
    const after = io.lstatSync(CONFIG_FILE); protectedStat(after, false);
    if (before.ino !== after.ino || before.dev !== after.dev || before.size !== after.size ||
        before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) throw new Error('Runtime configuration changed');
    return config;
  } catch (_) { throw new Error('Protected runtime configuration unavailable'); }
}
function readPortableConfig(environment = process.env, options) {
  const value = environment.DROIDDECK_PORTABLE_RUNTIME;
  if (value === undefined) return undefined;
  if (value !== '1') throw new Error('Invalid portable runtime opt-in');
  return readProtectedRuntimeConfig(options);
}
function main(args) {
  if (process.platform !== 'linux' || args.length !== 1 || args[0] !== 'inspect') throw new Error('Linux protected configuration inspect required');
  console.log(JSON.stringify({ config: readProtectedRuntimeConfig(), installable: false }, null, 2));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) { console.error('Protected runtime configuration rejected'); process.exitCode = 1; } }
module.exports = { readProtectedRuntimeConfig, readPortableConfig, CONFIG_FILE };
