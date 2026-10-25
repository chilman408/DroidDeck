// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawnSync } = require('node:child_process');
const { execute, parseProcessStart, readContainerIdentity, readAndroidIdentity } = require('./recovery-probe-io');
const proc = (pid, start = '123') => `${pid} (name with ) spaces) S ${Array(18).fill('0').join(' ')} ${start} 0\n`;

test('process birth parsing matches proc layout and refuses malformed or unbounded evidence', () => {
  assert.equal(parseProcessStart(proc(700), 700), '123');
  for (const text of [proc(701), proc(700, '0'), proc(700, 'bad'), 'malformed', 'x'.repeat(16385), proc(700) + '\0']) {
    assert.throws(() => parseProcessStart(text, 700));
  }
});
test('container identity reads only proc stat and net namespace', async () => {
  const calls = [], io = {
    readFile: async (name, options) => { calls.push(name); assert.equal(options.encoding, 'utf8'); return proc(700); },
    stat: async (name, options) => { calls.push(name); assert.equal(options.bigint, true); return { dev: 4n, ino: 987n }; },
  };
  assert.equal(await readContainerIdentity(700, { io }), '4:987:123');
  assert.deepEqual(calls, ['/proc/700/stat', '/proc/700/ns/net']);
  io.stat = async () => ({ dev: 4, ino: 987 });
  await assert.rejects(readContainerIdentity(700, { io }));
});
test('Android identities bind exactly one system server and compositor process birth', async () => {
  const calls = [], io = { readFile: async name => { calls.push(name); return proc(name.includes('/30/') ? 30 : 40); } };
  const run = async (file, args) => {
    assert.equal(file, '/usr/bin/nsenter');
    assert.deepEqual(args.slice(0, 8), ['-t', '700', '-m', '-p', '-r', '--', '/system/bin/toybox', 'pidof']);
    return args.at(-1) === 'system_server' ? '30\n' : '40\n';
  };
  assert.equal(await readAndroidIdentity(700, {}, { io, run }), '30:123/40:123');
  assert.deepEqual(calls, ['/proc/700/root/proc/30/stat', '/proc/700/root/proc/40/stat']);
});
test('bad host or ambiguous guest PID refuses before filesystem inspection', async () => {
  const io = { readFile: async () => assert.fail('unexpected read') };
  for (const pid of [0, 1, -1, 1.5, '700', Number.MAX_SAFE_INTEGER + 1]) {
    await assert.rejects(readContainerIdentity(pid, { io }));
    await assert.rejects(readAndroidIdentity(pid, {}, { io }));
  }
  for (const text of ['1', '30 40', '30;id', '0'.repeat(129), '9007199254740992']) {
    await assert.rejects(readAndroidIdentity(700, {}, { io, run: async () => text }));
  }
});
test('abort is checked before commands, reads and health publication', async () => {
  const controller = new AbortController(); controller.abort();
  const io = { readFile: async () => assert.fail('unexpected read') };
  await assert.rejects(readContainerIdentity(700, { io, signal: controller.signal }));
  await assert.rejects(readAndroidIdentity(700, { signal: controller.signal }, { io }));
  await assert.rejects(execute(process.execPath, [], { signal: controller.signal }));
});
test('executor closes inherited environment and never invokes a shell', async () => {
  process.env.DROIDDECK_PROBE_TEST_SENTINEL = 'must-not-inherit';
  let env;
  try { env = JSON.parse(await execute(process.execPath, ['-e', 'process.stdout.write(JSON.stringify(process.env))'])); }
  finally { delete process.env.DROIDDECK_PROBE_TEST_SENTINEL; }
  const expected = { PATH: '/usr/sbin:/usr/bin:/sbin:/bin', LANG: 'C', LC_ALL: 'C' };
  if (process.platform === 'linux') assert.deepEqual(env, expected);
  else for (const [key, value] of Object.entries(expected)) assert.equal(env[key], value);
  assert.equal(env.DROIDDECK_PROBE_TEST_SENTINEL, undefined);
  assert.equal((await execute(process.execPath, ['-e', 'process.stdout.write(process.argv[1])', 'literal;echo injected'])).toString(), 'literal;echo injected');
});
test('executor refuses timeout, stdout/stderr overflow and does not disclose guest errors', async () => {
  for (const value of [0, 10001, NaN, '10']) await assert.rejects(execute(process.execPath, [], { timeoutMs: value }));
  await assert.rejects(execute(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { timeoutMs: 100 }), /timed out/);
  await assert.rejects(execute(process.execPath, ['-e', 'process.stdout.write(Buffer.alloc(4*1024*1024+1))']), /bound/);
  await assert.rejects(execute(process.execPath, ['-e', 'process.stderr.write(Buffer.alloc(65537))']), /bound/);
  await assert.rejects(execute(process.execPath, ['-e', 'process.stderr.write("private guest error");process.exit(7)']),
    error => !error.message.includes('private guest error') && /failed/.test(error.message));
});
test('executor rejects cancellation and missing executable without lingering work', async () => {
  const controller = new AbortController();
  const running = execute(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { signal: controller.signal });
  controller.abort(); await assert.rejects(running);
  await assert.rejects(execute(path.join(os.tmpdir(), 'droiddeck-no-such-executable'), []));
});
test('isolated probe loads with all mutable display and third-party imports forbidden', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-probe-isolated-'));
  try {
    for (const name of ['gpu-native.js', 'cpu-fallback.js', 'recovery-probe-io.js']) {
      fs.copyFileSync(path.join(__dirname, name), path.join(root, name));
    }
    const code = `const M=require('node:module'),old=M._load;M._load=function(name,...args){
      if(!name.startsWith('node:')&&!name.startsWith('./')&&name!==process.argv[1])throw Error('Forbidden import');
      return old.call(this,name,...args)};
      const gpu=require(process.argv[1]);let calls=0;
      const io={readFile:async()=>{throw Error('Expected isolated proc read')}};
      gpu.inspectNativeGpu({io,run:async(file,args)=>{calls++;return args.at(-1)==='-sH'?'RUNNING':'700'}})
      .then(()=>process.exit(2),e=>{if(e.message!=='Expected isolated proc read'||calls!==2)process.exit(3)})`;
    const result = spawnSync(process.execPath, ['-e', code, path.join(root, 'gpu-native.js')], { encoding: 'utf8', windowsHide: true, timeout: 10000 });
    assert.equal(result.status, 0, result.stderr);
  } finally { fs.rmSync(root, { recursive: true }); }
});
