// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { renderPidCompatibility, preparePidCompatibility } = require('./pid-compat');

test('PID compatibility preserves existing configuration and is idempotent', () => {
  const original = '# Waydroid\nlxc.mount.auto = cgroup:ro sys:ro proc\nlxc.init.cmd = /init';
  const updated = renderPidCompatibility(original);
  assert.ok(updated.startsWith(original + '\n'));
  assert.match(updated, /^lxc.hook.pre-mount = \/usr\/bin\/node \/opt\/teslaandroid-waydroid\/runtime\/waydroid-display\/pid-compat-hook.js$/m);
  assert.equal(renderPidCompatibility(updated), updated);
  assert.throws(() => renderPidCompatibility('lxc.sysctl.kernel.pid_max = 65535\n'), /PID configuration/);
});

test('PID compatibility refuses conflicts, duplicate entries and invalid text', () => {
  for (const text of ['lxc.sysctl.kernel.pid_max = 4194304\n',
    'lxc.sysctl.kernel.pid_max = 65535\nlxc.sysctl.kernel.pid_max = 65535\n',
    'lxc.sysctl.kernel.pid_max = unknown', 'x\0y', 'x'.repeat(262145)]) {
    assert.throws(() => renderPidCompatibility(text));
  }
});

test('portable PID hook uses the reviewed DroidDeck root and rejects mixed roots or mode injection', () => {
  const updated = renderPidCompatibility('# existing\n', { portable: true });
  assert.match(updated, /^lxc.hook.pre-mount = \/usr\/bin\/node \/opt\/droiddeck\/runtime\/waydroid-display\/pid-compat-hook.js$/m);
  assert.equal(renderPidCompatibility(updated, { portable: true }), updated);
  assert.throws(() => renderPidCompatibility(renderPidCompatibility(''), { portable: true }), /PID configuration/);
  assert.throws(() => renderPidCompatibility(updated), /PID configuration/);
  assert.throws(() => renderPidCompatibility('', { portable: '1' }), /mode/);
});

async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'ta-pid-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const filename = path.join(directory, 'config');
  await fs.writeFile(filename, '# preserved\nlxc.include = ' + path.join(directory, 'config_nodes') + '\n', { mode: 0o644 });
  await fs.writeFile(path.join(directory, 'config_nodes'), 'lxc.mount.entry = /dev/null dev/null none bind 0 0\n', { mode: 0o644 });
  const options = { filename, expectedUid: process.getuid?.() ?? 0, boundary: directory };
  return { directory, filename, options };
}

test('PID compatibility atomically updates a real config without changing includes or mode', async t => {
  const { directory, filename, options } = await fixture(t);
  const original = await fs.readFile(filename, 'utf8');
  const included = await fs.readFile(path.join(directory, 'config_nodes'), 'utf8');
  assert.equal(await preparePidCompatibility(options), 'updated');
  assert.ok((await fs.readFile(filename, 'utf8')).startsWith(original));
  assert.equal(await fs.readFile(path.join(directory, 'config_nodes'), 'utf8'), included);
  const stat = await fs.stat(filename);
  if (process.platform !== 'win32') assert.equal(stat.mode & 0o777, 0o644);
  assert.equal(await preparePidCompatibility(options), 'unchanged');
  assert.deepEqual((await fs.readdir(directory)).sort(), ['config', 'config_nodes']);
});

test('portable PID preparation rewrites only the hook and stays idempotent with real files', async t => {
  const { filename, options } = await fixture(t);
  const before = await fs.readFile(filename, 'utf8');
  assert.equal(await preparePidCompatibility({ ...options, portable: true }), 'updated');
  assert.equal(await fs.readFile(filename, 'utf8'), renderPidCompatibility(before, { portable: true }));
  assert.equal(await preparePidCompatibility({ ...options, portable: true }), 'unchanged');
});

test('PID compatibility rejects a conflicting included config without changing the main config', async t => {
  const { directory, filename, options } = await fixture(t);
  const original = await fs.readFile(filename);
  await fs.writeFile(path.join(directory, 'config_nodes'), 'lxc.sysctl.kernel.pid_max = 4194304\n');
  await assert.rejects(preparePidCompatibility(options), /PID configuration/);
  assert.deepEqual(await fs.readFile(filename), original);
});

test('PID compatibility recognizes a single safe included hook without duplicating it', async t => {
  const { directory, filename, options } = await fixture(t);
  const original = await fs.readFile(filename);
  await fs.writeFile(path.join(directory, 'config_nodes'), renderPidCompatibility(''));
  assert.equal(await preparePidCompatibility(options), 'unchanged');
  assert.deepEqual(await fs.readFile(filename), original);
});

test('PID compatibility refuses linked, writable and outside-boundary configurations', async t => {
  const { directory, filename, options } = await fixture(t);
  await fs.link(filename, path.join(directory, 'config_copy'));
  await assert.rejects(preparePidCompatibility(options), /protected/);
  await fs.unlink(path.join(directory, 'config_copy'));
  await fs.writeFile(filename, 'lxc.include = /outside/config\n');
  await assert.rejects(preparePidCompatibility(options), /include/);
  if (process.platform !== 'win32') {
    await fs.writeFile(filename, '# preserved\n');
    await fs.chmod(filename, 0o666);
    await assert.rejects(preparePidCompatibility(options), /protected/);
    await fs.chmod(filename, 0o644);
    await fs.rename(filename, path.join(directory, 'original'));
    await fs.symlink(path.join(directory, 'original'), filename);
    await assert.rejects(preparePidCompatibility(options), /protected/);
  }
});

test('PID compatibility refuses invalid UTF-8 and recursive include cycles', async t => {
  const { directory, filename, options } = await fixture(t);
  await fs.writeFile(filename, Buffer.from([0xff]));
  await assert.rejects(preparePidCompatibility(options));
  await fs.writeFile(filename, 'lxc.include = ' + path.join(directory, 'config_nodes') + '\n');
  await fs.writeFile(path.join(directory, 'config_nodes'), 'lxc.include = ' + filename + '\n');
  await assert.rejects(preparePidCompatibility(options), /include/);
});
