// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const { deploymentProfile, validateArtifact } = require('./deploy-auto-h264-recovery');
const fs = require('node:fs'), path = require('node:path');
test('new logo profile replaces only selected image and About contrast styling in a separate backup', () => {
  const profile = deploymentProfile(['--droiddeck-logo']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/logo-stage-20261017fb');
  assert.equal(profile.backup, '/var/lib/teslaandroid-waydroid/backups/logo-refresh-20261017fb');
  assert.deepEqual(profile.files, [{ file: 'assets/droiddeck-logo.png',
    before: '6dfbe17b8b505c1bd5a334c067c96d4f1a7d0d00390b6a5f163d1fa2547dbc30',
    after: 'ea8e12af127eba9bc533d805770e06e3da9f7a0f277f36a2eac574c363fdeb29' },
  { file: 'css/app.css', before: '4af5930954a527f41c4b6a1d3ae263b8618ebd4be4d1369b62c8661af79e6b95',
    after: '81e0cd8fdcb17fd94c2e938b309adca674959d21e562bcf57fa97435e15f42e1' }]);
  assert.notEqual(profile.backup, deploymentProfile(['--droiddeck-brand']).backup);
  assert.equal(deploymentProfile(['--droiddeck-logo', '--rollback']).backup, profile.backup);
  assert.equal(deploymentProfile(['--droiddeck-logo', '--rollback']).rollback, true);
  for (const args of [['--droiddeck-logo', '/tmp/other'], ['--droiddeck-logo', '--install'],
    ['--droiddeck-logo', '--rollback', 'other']]) assert.throws(() => deploymentProfile(args));
});
test('new profile accepts only the pinned selected asset and not old or arbitrary bytes', () => {
  const [file] = deploymentProfile(['--droiddeck-logo']).files;
  const image = fs.readFileSync(path.join(__dirname, '../runtime/branding/droiddeck-logo.png'));
  validateArtifact(image, file.after);
  assert.throws(() => validateArtifact(image, file.before));
  assert.throws(() => validateArtifact(Buffer.from('other'), file.after));
});
