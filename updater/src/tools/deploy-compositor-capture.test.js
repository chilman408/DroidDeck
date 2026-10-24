// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { validateManifest, files, BACKUP } = require('./deploy-compositor-capture');
test('capture rollback accepts only exact code targets, never preference paths', () => {
  const manifest = { files: files.map(([, target], index) => ({ target, saved: BACKUP + '/code-' + index,
    existed: true, installedSha256: 'a'.repeat(64), previousSha256: 'b'.repeat(64) })) };
  validateManifest(manifest);
  for (const mutation of [record => { record.target = '/var/lib/teslaandroid-waydroid/display.json'; },
    record => { record.saved = '/etc/passwd'; }, record => { record.previousSha256 = null; }, record => { record.existed = 'yes'; }]) {
    const invalid = structuredClone(manifest); mutation(invalid.files[0]);
    assert.throws(() => validateManifest(invalid), /backup target/);
  }
});
