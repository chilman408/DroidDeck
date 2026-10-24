// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { armFirstFrameRepaint, repaintDisplay } = require('./compositor-video-launcher');
function fixture() {
  const child = new EventEmitter(); child.stdout = new EventEmitter();
  let callback, cleared = false, calls = 0;
  armFirstFrameRepaint(child, async () => { calls++; }, {
    setTimeout(fn, ms) { assert.equal(ms, 2000); callback = fn; return { unref() {} }; },
    clearTimeout() { cleared = true; }, onFailure() { throw new Error('Unexpected failure'); },
  });
  return { child, fire: () => { if (!cleared) callback(); }, calls: () => calls, cleared: () => cleared };
}
test('a cold compositor subscription receives one bounded repaint, never periodic input', async () => {
  const f = fixture(); f.fire(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.calls(), 1); assert.equal(f.child.stdout.listenerCount('data'), 0);
});
test('a first keyframe cancels repaint, including a marker split across stdout chunks', () => {
  const f = fixture(); f.child.stdout.emit('data', Buffer.from('Compositor H264 configuration and '));
  f.child.stdout.emit('data', Buffer.from('first key frame ready\n'));
  f.fire(); assert.equal(f.calls(), 0); assert.equal(f.cleared(), true);
});
test('retired or failed capture children cannot repaint Android later', () => {
  for (const event of ['exit', 'error']) {
    const f = fixture(); f.child.emit(event); f.fire(); assert.equal(f.calls(), 0);
    assert.equal(f.child.stdout.listenerCount('data'), 0);
  }
});
test('repaint uses only the fenced transient shade commands, never settings or navigation writes', async () => {
  const commands = [];
  const run = async (file, args) => {
    if (file === '/usr/bin/lxc-info') return Buffer.from('1234\n');
    assert.equal(file, '/usr/bin/nsenter');
    assert.deepEqual(args.slice(0, 8), ['-t', '1234', '-m', '-p', '-r', '--', '/system/bin/cmd', 'statusbar']);
    commands.push(args.at(-1)); return Buffer.alloc(0);
  };
  await repaintDisplay(run, '1234', async () => true, async ms => assert.equal(ms, 500));
  assert.deepEqual(commands, ['expand-notifications', 'collapse']);
  commands.length = 0;
  await assert.rejects(repaintDisplay(run, '1234', async () => true, async () => { throw new Error('interrupted'); }), /interrupted/);
  assert.deepEqual(commands, ['expand-notifications', 'collapse']);
});
test('changed container identity or revoked native lease cannot receive a repaint', async () => {
  for (const [pid, owner] of [['5678', true], ['1234', false]]) {
    await assert.rejects(repaintDisplay(async file => {
      assert.equal(file, '/usr/bin/lxc-info'); return Buffer.from(pid);
    }, '1234', async () => owner), /ownership/);
  }
});
