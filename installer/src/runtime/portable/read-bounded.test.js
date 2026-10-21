// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), cp = require('node:child_process');
const { readBoundedRegular } = require('./read-bounded');
function temporary(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-read-'));
  try { run(dir); } finally {
    assert.equal(path.dirname(path.resolve(dir)), path.resolve(os.tmpdir()));
    assert.match(path.basename(dir), /^droiddeck-read-/); fs.rmSync(dir, { recursive: true, force: true });
  }
}
test('bounded reads preserve binary bytes and enforce exact boundary and regular-file requirements', () => temporary(dir => {
  const file = path.join(dir, 'fixture'); fs.writeFileSync(file, Buffer.from([0, 255, 13, 10]));
  assert.deepEqual(readBoundedRegular(file, 4), Buffer.from([0, 255, 13, 10]));
  for (const bound of [3, 0, -1, NaN, '4', 8 * 1024 * 1024 + 1]) assert.throws(() => readBoundedRegular(file, bound));
  assert.throws(() => readBoundedRegular(dir, 4));
  fs.writeFileSync(file, Buffer.alloc(0)); assert.deepEqual(readBoundedRegular(file, 4), Buffer.alloc(0));
}));
test('bounded read rechecks final link metadata and closes descriptor on rejection', () => temporary(dir => {
  const file = path.join(dir, 'fixture'); fs.writeFileSync(file, 'safe');
  const script = `const fs=require('fs'),assert=require('assert/strict'),stat=fs.fstatSync,close=fs.closeSync;
    let count=0,closed=0;fs.fstatSync=function(fd){const s=stat(fd);if(++count===2)s.nlink=2;return s;};
    fs.closeSync=function(fd){closed++;return close(fd);};
    const {readBoundedRegular}=require(process.argv[2]);assert.throws(()=>readBoundedRegular(process.argv[1],8));assert.equal(closed,1);`;
  const result = cp.spawnSync(process.execPath, ['-e', script, file, require.resolve('./read-bounded')], { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 0, result.stderr);
}));
