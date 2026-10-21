// SPDX-License-Identifier: GPL-3.0-only
const { preparePidCompatibility } = require('../waydroid-display/pid-compat');
async function startPidPreparation({ read, prepare = preparePidCompatibility } = {}) {
  read ||= () => {
    const base = '/opt/droiddeck/runtime/portable/';
    const { loadWorkerConfiguration } = require(base + 'worker-config');
    const { readProtectedRuntimeConfig } = require(base + 'protected-config');
    return loadWorkerConfiguration({ role: 'privileged', read: () => readProtectedRuntimeConfig(),
      expectedDigest: process.env.DROIDDECK_CONFIGURATION_SHA256 });
  };
  if (!await read()) throw new Error('Protected portable configuration required');
  return prepare({ portable: true });
}
async function main(args = process.argv.slice(2)) {
  if (process.platform !== 'linux' || process.getuid() !== 0 || args.length) throw new Error('Fixed root PID startup required');
  console.log('Portable guest PID compatibility: ' + await startPidPreparation());
}
if (require.main === module) main().catch(() => { console.error('Portable PID startup rejected'); process.exitCode = 1; });
module.exports = { startPidPreparation, main };
