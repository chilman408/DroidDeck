// SPDX-License-Identifier: GPL-3.0-only
const { loadWorkerConfiguration } = require('./worker-config');
const { readProtectedRuntimeConfig } = require('./protected-config');
const WORKERS = Object.freeze(Object.fromEntries([
  ['runtime', 'privileged', '../waydroid-display/runtime-main'],
  ['display', 'privileged', '../waydroid-display/helper'],
  ['gps', 'privileged', '../waydroid-display/gps-helper'],
  ['connection', 'privileged', '../browser-connection/helper'],
  ['video', 'privileged', '../preview-gateway/video-launcher'],
  ['compositor-video', 'privileged', '../preview-gateway/compositor-video-launcher'],
  ['audio', 'privileged', '../preview-gateway/audio-launcher'],
  ['video-relay', 'user', '../preview-gateway/h264-relay'],
  ['audio-relay', 'user', '../preview-gateway/audio-relay'],
  ['gateway', 'user', '../preview-gateway/server'],
  ['compositor-check', 'user', './desktop-preflight'],
  ['headless-check', 'user', '../waydroid-display/headless-session-preflight'],
].map(([name, role, module]) => [name, Object.freeze({ role, module })])));
async function dispatch(name, options) {
  const worker = require(WORKERS[name].module);
  if (name === 'runtime') {
    let previous;
    return worker.runApplication({ configuration: options.configuration, signal: options.signal,
      report: phase => { if (phase !== previous) { previous = phase; console.log('Runtime ' + phase); } } });
  }
  return worker.main(options);
}
async function startWorker(name, { uid = process.getuid?.(), read = readProtectedRuntimeConfig, validateAccount,
  signal, launch = dispatch, expectedDigest = process.env.DROIDDECK_CONFIGURATION_SHA256 } = {}) {
  signal?.throwIfAborted();
  if (typeof name !== 'string' || !Object.hasOwn(WORKERS, name)) throw new Error('Unknown portable worker');
  const configuration = await loadWorkerConfiguration({ role: WORKERS[name].role, uid, read: () => read(), validateAccount, signal, expectedDigest });
  if (!configuration) throw new Error('Protected portable configuration required');
  return launch(name, Object.freeze({ configuration, args: Object.freeze([]), signal }));
}
async function main(args) {
  if (process.platform !== 'linux' || args.length !== 1) throw new Error('Linux portable worker name required');
  if (args[0] !== 'runtime') return startWorker(args[0]);
  const controller = new AbortController();
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => controller.abort(new Error('Worker stopped')));
  return startWorker(args[0], { signal: controller.signal });
}
if (require.main === module) main(process.argv.slice(2)).catch(() => { console.error('Portable worker startup rejected'); process.exitCode = 1; });
module.exports = { startWorker, WORKERS };
