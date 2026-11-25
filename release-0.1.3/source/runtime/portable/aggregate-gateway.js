// SPDX-License-Identifier: GPL-3.0-only
// Called only from an independently authenticated aggregate runtime view.
// This adapter has no staging, signing, enrollment or activation authority.
function absoluteDirectory(value) {
  return typeof value === 'string' && value.length < 256 && value.startsWith('/') &&
    value.slice(1).split('/').every(part => /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(part) && part !== '.' && part !== '..');
}
async function startGateway({ mode, frontendDir, args = [], environment = process.env, uid = process.getuid?.(), signal,
  server, portable, loadLegacy } = {}) {
  signal?.throwIfAborted();
  if (!['portable', 'legacy'].includes(mode) || typeof frontendDir !== 'string' ||
      !/^\/var\/lib\/droidmate-browser\/[a-f0-9]{64}\/frontend$/.test(frontendDir) || !Array.isArray(args) ||
      !Number.isSafeInteger(uid) || uid < 0) throw Error('Invalid aggregate browser entry');
  const optIn = environment.DROIDDECK_PORTABLE_RUNTIME, digest = environment.DROIDDECK_CONFIGURATION_SHA256;
  if (mode === 'portable') {
    if (optIn !== '1' || typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest) || args.length) {
      throw Error('Protected portable browser entry required');
    }
  } else if (optIn !== undefined || digest !== undefined || uid !== 1000 || args.length !== 2 ||
      !absoluteDirectory(args[0]) || typeof args[1] !== 'string' || !/^[1-9][0-9]{0,4}$/.test(args[1]) || Number(args[1]) > 65535) {
    throw Error('Original legacy browser entry required');
  }
  const originalArgs = Object.freeze([...args]);
  async function launch(name, options) {
    signal?.throwIfAborted();
    if (name !== 'gateway') throw Error('Aggregate browser role mismatch');
    const gateway = server ?? require('../preview-gateway/server');
    return gateway.main({ ...options,
      create: original => { signal?.throwIfAborted(); return gateway.createGateway({ ...original, frontendDir }); } });
  }
  if (mode === 'portable') {
    return (portable ?? require('./worker-entry').startWorker)('gateway', { uid, signal, expectedDigest: digest, launch });
  }
  const configuration = await (loadLegacy ?? require('./worker-config').loadWorkerConfiguration)({
    role: 'user', uid, environment, signal, read: () => undefined });
  if (configuration !== undefined) throw Error('Unexpected portable legacy configuration');
  return launch('gateway', { args: originalArgs, signal });
}
async function main(args) {
  if (process.platform !== 'linux' || !Array.isArray(args) || args.length < 2) throw Error('Linux aggregate browser entry required');
  const [mode, frontendDir, ...originalArgs] = args;
  return startGateway({ mode, frontendDir, args: originalArgs });
}
if (require.main === module) main(process.argv.slice(2)).catch(() => {
  console.error('Aggregate browser startup rejected'); process.exitCode = 1;
});
module.exports = { startGateway };
