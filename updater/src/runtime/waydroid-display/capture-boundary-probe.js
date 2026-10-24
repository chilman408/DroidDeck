// SPDX-License-Identifier: GPL-3.0-only
const { execute } = require('./host-adapter');
const APPS = Object.freeze(['com.netflix.mediaclient', 'com.amazon.avod.thirdpartyclient',
  'com.disney.disneyplus', 'com.hulu.plus', 'com.google.android.youtube']);
function bounded(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 4 * 1024 * 1024) throw new Error('Invalid diagnostic dump');
  return text;
}
function booleanField(block, field) {
  const value = new RegExp('(?:^|\\s)' + field + '=(true|false)(?=\\s|$)', 'm').exec(block)?.[1];
  return value === undefined ? null : value === 'true';
}
function windowAttributes(block) {
  const start = /\bmAttrs=\{/.exec(block);
  if (!start) return null;
  let depth = 1;
  for (let i = start.index + start[0].length; i < block.length; i++) {
    if (block[i] === '{') depth++;
    else if (block[i] === '}' && --depth === 0) return block.slice(start.index, i + 1);
  }
  return null;
}
function windowFacts(text) {
  const result = [];
  for (const block of bounded(text).split(/(?=^  Window #\d+ Window\{)/m).slice(0, 513)) {
    const header = /^  Window #\d+ Window\{[^\n]+\}:\s*$/m.exec(block)?.[0];
    if (!header) continue;
    const app = APPS.find(name => header.includes(name + '/'));
    if (!app) continue;
    const attributes = windowAttributes(block);
    const flags = attributes && /^\s*fl=([^\r\n]+)/m.exec(attributes)?.[1];
    const secure = flags === undefined || flags === null ? null : /^0x[0-9a-f]+$/i.test(flags.trim()) ?
      Boolean(Number.parseInt(flags, 16) & 0x2000) : /(?:^|\s)SECURE(?:\s|$)/.test(flags);
    const surface = booleanField(block, 'mHasSurface'), visible = booleanField(block, 'isVisible');
    const onScreen = booleanField(block, 'isOnScreen');
    result.push({ app, displayId: Number(/\bmDisplayId=(\d+)/.exec(block)?.[1] ?? -1),
      surface, visible, onScreen, active: surface === true && visible === true && onScreen === true, secure });
  }
  return result;
}
function compositionFacts(text) {
  text = bounded(text);
  const displays = [];
  for (const match of text.matchAll(/^Display (\d+) \((physical|virtual), "([^"\r\n]+)"\)\r?\n\s+Composition Display State:\r?\n\s+isEnabled=(true|false) isSecure=(true|false)/gm)) {
    displays.push({ id: match[1], type: match[2], capture: match[3] === 'scrcpy',
      enabled: match[4] === 'true', secure: match[5] === 'true' });
    if (displays.length === 32) break;
  }
  const layers = [], seen = new Set();
  for (const match of text.matchAll(/^\s{2}Layer \[(\d+)(?: mirrored from \d+,)?\] (.*)$/gm)) {
    const app = APPS.find(name => match[2].includes(name));
    if (!app) continue;
    const secure = /(?:^|\s)\(Secure\)(?:\s|$)/.test(match[2]);
    const key = app + ':' + match[1] + ':' + secure;
    if (!seen.has(key)) { seen.add(key); layers.push({ app, id: match[1], secure }); }
    if (layers.length === 128) break;
  }
  return { displays, layers };
}
async function inspectCaptureBoundary(run = execute) {
  const options = { env: { ...process.env, LD_LIBRARY_PATH: '/opt/teslaandroid-waydroid/libgbinder' }, timeoutMs: 10000 };
  const discover = async () => {
    const pid = (await run('/usr/bin/lxc-info', ['-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], options)).toString().trim();
    if (!/^[1-9]\d*$/.test(pid) || !Number.isSafeInteger(Number(pid)) || Number(pid) < 2) throw new Error('Invalid container identity');
    return pid;
  };
  const pid = await discover();
  const dump = async args => (await run('/usr/bin/nsenter', ['-t', pid, '-m', '-p', '-r', '--', '/system/bin/dumpsys', ...args], options)).toString();
  const windows = windowFacts(await dump(['window', 'windows']));
  const composition = compositionFacts(await dump(['SurfaceFlinger']));
  if (await discover() !== pid) throw new Error('Container changed during diagnostic');
  return { version: 1, windows, ...composition, protectedBufferStatus: 'unknown',
    scope: 'Window flags and layer metadata only; no pixels, accounts, licenses or app changes' };
}
async function main() {
  if (process.platform !== 'linux' || process.getuid?.() !== 0 || process.argv.length !== 2) throw new Error('Read-only probe requires Linux root and no arguments');
  console.log(JSON.stringify(await inspectCaptureBoundary()));
}
if (require.main === module) main().catch(() => { console.error('Capture-boundary inspection failed; no changes made'); process.exitCode = 1; });
module.exports = { windowFacts, compositionFacts, inspectCaptureBoundary };
