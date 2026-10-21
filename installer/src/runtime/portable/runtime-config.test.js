// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const cp = require('node:child_process');
const { normalizeRuntimeConfig, deriveRuntimeLayout, readRuntimeConfig } = require('./runtime-config');
const base = { user: 'deck', uid: 1200, gid: 1201, lanAddress: '192.168.5.10' };
function temporary(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-config-'));
  try { run(dir); } finally {
    assert.equal(path.dirname(path.resolve(dir)), path.resolve(os.tmpdir()));
    assert.match(path.basename(dir), /^droiddeck-config-/); fs.rmSync(dir, { recursive: true, force: true });
  }
}
test('default portable config has no current VM identity and derives immutable paths', () => {
  const config = normalizeRuntimeConfig(base), layout = deriveRuntimeLayout(config);
  assert.deepEqual(config, { version: 1, ...base, home: '/home/deck', port: 8443, hostname: 'device.teslaandroid.com' });
  assert.equal(layout.root, '/opt/droiddeck'); assert.equal(layout.frontend, '/opt/droiddeck/frontend');
  assert.equal(layout.preferences, '/home/deck/.config/droiddeck');
  assert.equal(layout.runtimeDirectory, '/run/user/1200'); assert.equal(layout.dbusAddress, 'unix:path=/run/user/1200/bus');
  assert.equal(layout.gatewayOrigin, 'https://192.168.5.10:8443'); assert.equal(layout.publicOrigin, 'https://device.teslaandroid.com');
  assert.equal(Object.isFrozen(config), true); assert.equal(Object.isFrozen(layout), true);
  assert.doesNotMatch(JSON.stringify(layout), /hilman|192\.168\.2\.90|\/run\/user\/1000/);
});
test('alternate LAN identity, custom home and IDNA hostname normalize without changing inputs', () => {
  const input = { ...base, home: '/home/stream-user', port: 9443, lanAddress: '10.5.8.20', hostname: 'B\u00dcCHER.example' };
  const before = structuredClone(input), config = normalizeRuntimeConfig(input);
  assert.deepEqual(input, before); assert.equal(config.hostname, 'xn--bcher-kva.example');
  const layout = deriveRuntimeLayout(config);
  assert.equal(layout.keyFile, '/home/stream-user/.config/droiddeck/key.pem');
  assert.equal(layout.certificateFile, '/home/stream-user/.config/droiddeck/cert.pem');
  assert.equal(layout.gatewayOrigin, 'https://10.5.8.20:9443');
});
test('strict schema rejects unknown secrets, omissions and scalar coercion', () => {
  for (const value of [null, [], 'config', 1, { ...base, password: 'secret' }, { ...base, certificatePem: 'secret' },
    { ...base, version: '1' }, { ...base, uid: '1200' }, { ...base, gid: 1.5 }, { ...base, port: '8443' }]) assert.throws(() => normalizeRuntimeConfig(value));
  for (const key of ['user', 'uid', 'gid', 'lanAddress']) { const input = { ...base }; delete input[key]; assert.throws(() => normalizeRuntimeConfig(input)); }
});
test('runtime configuration fences identity, path, address and port injection classes', () => {
  const invalid = {
    user: ['root', 'UPPER', '-deck', 'deck\nUser=root', 'deck%u', 'a'.repeat(33)],
    home: ['/root', '/home/deck/../root', '/home/deck/', '/home/two words', '/home/%u', '/home/CON', '/home/deck.'],
    uid: [0, 999, 2147483648, NaN, Infinity, -1, 1000.5], gid: [0, 999, 2147483648],
    lanAddress: ['0.0.0.0', '127.0.0.1', '8.8.8.8', '192.168.01.2', '192.168.2.255', '10.0.0.0', '172.15.1.2', '172.32.1.2', '::1', 'host', '10.1.2.3\nX=1'],
    port: [0, 443, 65536, 8443.5, NaN], hostname: ['https://deck.example', 'deck.example:8443', '192.168.2.4', '*.example'],
  };
  for (const [field, values] of Object.entries(invalid)) for (const value of values) assert.throws(() => normalizeRuntimeConfig({ ...base, [field]: value }), field + ': ' + value);
  for (const lanAddress of ['10.0.0.1', '172.16.0.1', '172.31.255.254', '192.168.255.254']) assert.equal(normalizeRuntimeConfig({ ...base, lanAddress }).lanAddress, lanAddress);
});
test('bounded config reader rejects malformed, oversized and multiply linked input', () => temporary(dir => {
  const file = path.join(dir, 'config.json'); fs.writeFileSync(file, JSON.stringify(base));
  assert.deepEqual(readRuntimeConfig(file), normalizeRuntimeConfig(base));
  fs.writeFileSync(file, '{'); assert.throws(() => readRuntimeConfig(file));
  fs.writeFileSync(file, Buffer.alloc(16385)); assert.throws(() => readRuntimeConfig(file));
  fs.writeFileSync(file, JSON.stringify(base)); fs.linkSync(file, path.join(dir, 'alias')); assert.throws(() => readRuntimeConfig(file));
}));
test('config reader rejects symbolic links', { skip: process.platform === 'win32' }, () => temporary(dir => {
  const target = path.join(dir, 'target.json'), file = path.join(dir, 'config.json');
  fs.writeFileSync(target, JSON.stringify(base)); fs.symlinkSync(target, file); assert.throws(() => readRuntimeConfig(file));
}));
test('malformed configuration errors never echo input secrets', () => temporary(dir => {
  const file = path.join(dir, 'config.json'); fs.writeFileSync(file, '{"password":s3cr3t-token-value}');
  assert.throws(() => readRuntimeConfig(file), error => !/password|s3cr3t|token-value/.test(error.message));
}));
test('configuration reads stay bounded when the file grows after its initial stat', () => temporary(dir => {
  const file = path.join(dir, 'config.json'); fs.writeFileSync(file, JSON.stringify(base));
  const script = `const fs=require('fs'),assert=require('assert/strict');
    const file=process.argv[1],module=process.argv[2],readAll=fs.readFileSync,read=fs.readSync;
    let unbounded=false,maxRead=0,grown=false;
    fs.readFileSync=function(...args){if(typeof args[0]==='number'){unbounded=true;return Buffer.alloc(1024*1024);}return readAll.apply(this,args);};
    fs.readSync=function(fd,buffer,offset,length,position){maxRead=Math.max(maxRead,length);if(!grown){grown=true;fs.appendFileSync(file,Buffer.alloc(1024*1024));}return read.call(this,fd,buffer,offset,length,position);};
    const {readRuntimeConfig}=require(module);assert.throws(()=>readRuntimeConfig(file));
    assert.equal(unbounded,false);assert.ok(maxRead>0&&maxRead<=16385);`;
  const result = cp.spawnSync(process.execPath, ['-e', script, file, require.resolve('./runtime-config')], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 0, result.stderr);
}));
