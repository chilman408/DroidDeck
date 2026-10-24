// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), crypto = require('node:crypto');
const { FILES, validateArtifact, deploymentProfile } = require('./deploy-auto-h264-recovery');

test('live-window rollout pins only the accepted legacy relay and renderer manager in new backups', () => {
  const runtime = deploymentProfile(['--live-window-runtime']), frontend = deploymentProfile(['--live-window-frontend']);
  assert.deepEqual(runtime.files.map(f => f.file), ['runtime/preview-gateway/h264-relay.js']);
  assert.deepEqual(frontend.files.map(f => f.file), ['js/display/display-renderer-manager.js']);
  assert.equal(runtime.files[0].before, '7b547ce2d4794ad890c66170126ca784936c721b857b7431aa8b1feee05c17d1');
  assert.equal(frontend.files[0].before, deploymentProfile(['--legacy-default']).files[0].after);
  assert.notEqual(runtime.backup, frontend.backup);
  assert.equal(deploymentProfile(['--live-window-runtime', '--rollback']).backup, runtime.backup);
  assert.throws(() => deploymentProfile(['--live-window-runtime', '--startup']));
});

test('cached-role startup correction touches one frontend file and rolls back to the accepted role handoff code', () => {
  const profile = deploymentProfile(['--display-role-startup']);
  assert.deepEqual(profile.files.map(item => item.file), ['js/display/display-controller.js']);
  assert.equal(profile.files[0].before, deploymentProfile(['--display-role-frontend']).files[1].after);
  assert.notEqual(profile.backup, deploymentProfile(['--display-role-frontend']).backup);
  assert.equal(deploymentProfile(['--display-role-startup', '--rollback']).backup, profile.backup);
  assert.throws(() => deploymentProfile(['--display-role-startup', '--startup']));
});

test('display role deployment fences five code files in independent backups and cannot activate portable gateway code', () => {
  const runtime = deploymentProfile(['--display-role-runtime']), frontend = deploymentProfile(['--display-role-frontend']);
  assert.deepEqual(runtime.files.map(item => item.file), ['runtime/preview-gateway/display-control.js', 'runtime/preview-gateway/server.js']);
  assert.equal(frontend.files.length, 3); assert.notEqual(runtime.backup, frontend.backup);
  assert.equal(runtime.files[1].before, deploymentProfile(['--browser-connection-runtime']).files[3].after);
  assert.equal(deploymentProfile(['--display-role-runtime', '--rollback']).backup, runtime.backup);
  assert.throws(() => deploymentProfile(['--display-role-runtime', '--startup']));
  const patch = require('node:fs').readFileSync(require('node:path').join(__dirname, '../runtime/patches/backend-display-role-claim.patch'), 'utf8');
  assert.match(patch, /claim,generation,role/); assert.doesNotMatch(patch, /portable\/|loadWorkerConfiguration|deriveRuntimeLayout/);
});

test('role runtime changes require both parent and gateway stopped on apply and rollback', async () => {
  const { guardDisplayRoleRuntime } = require('./deploy-auto-h264-recovery');
  await guardDisplayRoleRuntime(async () => 'inactive\ninactive\n');
  for (const states of ['active\ninactive', 'inactive\nactive', 'failed\ninactive', 'inactive', '']) await assert.rejects(guardDisplayRoleRuntime(async () => states));
});
test('frontend-only deployment pins both original and candidate bytes, with no runtime or preference targets', () => {
  assert.deepEqual(FILES.map(item => item.file), ['js/display/display-renderer-manager.js',
    'js/display/renderers/h264-webcodecs/h264WebCodecs.js']);
  for (const item of FILES) {
    assert.match(item.before, /^[a-f0-9]{64}$/); assert.match(item.after, /^[a-f0-9]{64}$/);
    assert.notEqual(item.before, item.after);
  }
});
test('role-startup deployment has a distinct single-file backup and cannot select arbitrary paths', () => {
  const initial = deploymentProfile([]), startup = deploymentProfile(['--startup']);
  assert.notEqual(initial.stage, startup.stage); assert.notEqual(initial.backup, startup.backup);
  assert.deepEqual(startup.files.map(item => item.file), ['js/display/display-controller.js']);
  assert.equal(startup.rollback, false);
  assert.equal(deploymentProfile(['--startup', '--rollback']).rollback, true);
  assert.equal(deploymentProfile(['--rollback']).backup, initial.backup);
  for (const args of [['--other'], ['--rollback', '--startup'], ['--startup', '/tmp/other']]) {
    assert.throws(() => deploymentProfile(args), /profile/);
  }
});
test('corrupt, non-buffer, oversized and unpinned deployment candidates fail closed', () => {
  const data = Buffer.from('candidate'), hash = crypto.createHash('sha256').update(data).digest('hex');
  assert.doesNotThrow(() => validateArtifact(data, hash));
  for (const candidate of [Buffer.from('changed'), 'candidate', Buffer.alloc(1024 * 1024 + 1)]) {
    assert.throws(() => validateArtifact(candidate, hash), /checksum/);
  }
});
test('legacy-default rollback cannot target the original renderer or startup backup', () => {
  const legacy = deploymentProfile(['--legacy-default']), rollback = deploymentProfile(['--legacy-default', '--rollback']);
  assert.deepEqual(legacy.files.map(item => item.file), ['js/display/display-renderer-manager.js']);
  assert.notEqual(legacy.backup, deploymentProfile([]).backup);
  assert.notEqual(legacy.backup, deploymentProfile(['--startup']).backup);
  assert.equal(rollback.backup, legacy.backup); assert.equal(rollback.rollback, true);
  assert.throws(() => deploymentProfile(['--legacy-default', '--startup']), /profile/);
});

test('supported-aspects deploys only the controller with its own fenced code backup', () => {
  const profile = deploymentProfile(['--supported-aspects']);
  assert.deepEqual(profile.files.map(item => item.file), ['js/display/display-controller.js']);
  assert.equal(profile.files[0].before, 'eaa5d663241b6198dc07cec3a932778a01ec299bcd78142894e31698f5cca96c');
  assert.match(profile.files[0].after, /^[a-f0-9]{64}$/);
  for (const args of [[], ['--startup'], ['--legacy-default']]) {
    assert.notEqual(profile.backup, deploymentProfile(args).backup);
    assert.notEqual(profile.stage, deploymentProfile(args).stage);
  }
  assert.equal(deploymentProfile(['--supported-aspects', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--supported-aspects', '--startup']), /profile/);
});

test('capture-preroll profile fences only the protected encoder, not Android or audio', () => {
  const profile = deploymentProfile(['--capture-preroll']);
  assert.equal(profile.root, '/opt/teslaandroid-waydroid');
  assert.deepEqual(profile.files.map(item => item.file), ['runtime/preview-gateway/compositor-h264.py']);
  assert.notEqual(profile.backup, deploymentProfile(['--supported-aspects']).backup);
  assert.equal(deploymentProfile(['--capture-preroll', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--capture-preroll', '/tmp/other']), /profile/);
});

test('capture-repaint has a separate launcher-only backup and cannot restore rejected preroll code', () => {
  const profile = deploymentProfile(['--capture-repaint']);
  assert.equal(profile.root, '/opt/teslaandroid-waydroid');
  assert.deepEqual(profile.files.map(item => item.file), ['runtime/preview-gateway/compositor-video-launcher.js']);
  assert.notEqual(profile.backup, deploymentProfile(['--capture-preroll']).backup);
  assert.equal(deploymentProfile(['--capture-repaint', '--rollback']).backup, profile.backup);
  assert.throws(() => deploymentProfile(['--capture-repaint', '--capture-preroll']), /profile/);
});

test('legacy-backpressure-v2 pins the matched worker before the renderer in a distinct backup', () => {
  const profile = deploymentProfile(['--legacy-backpressure-v2']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/legacy-backpressure-stage-20261011p2v2');
  assert.deepEqual(profile.files.map(item => item.file), [
    'js/display/renderers/h264-broadway/h264BrodwayWorker.js',
    'js/display/renderers/h264-broadway/h264Brodway.js',
  ]);
  assert.equal(profile.files[0].before, '8bf8a3e7701b836ea2a5153523811b07d673683bc99136e6a504ffe8ebce56d3');
  assert.equal(profile.files[0].after, 'f34045ad9d0c10aed19bb9c5d2e621dd96779d56c74e5040e338a09409acb280');
  assert.equal(profile.files[1].before, 'ed2038355dfc8e1fd3279e4d7b17ad593955a36676284a8747325fbadc0fb7d2');
  assert.equal(profile.files[1].after, 'a42d5e5a1b15383c3d96a40fc14d63d03dd4f789769ef58eb628c6ad7f58b1d6');
  assert.match(profile.backup, /legacy-backpressure-20261011p2$/);
  assert.notEqual(profile.backup, deploymentProfile(['--legacy-default']).backup);
  assert.equal(deploymentProfile(['--legacy-backpressure-v2', '--rollback']).backup, profile.backup);
  assert.equal(deploymentProfile(['--legacy-backpressure-v2', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--legacy-backpressure-v2', '--startup']), /profile/);
});

test('capture-backpressure changes only the encoder and cannot reuse previous capture backups', () => {
  const profile = deploymentProfile(['--capture-backpressure']);
  assert.equal(profile.root, '/opt/teslaandroid-waydroid');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/stream-cadence-stage-20261011q');
  assert.deepEqual(profile.files.map(item => item.file), ['runtime/preview-gateway/compositor-h264.py']);
  assert.equal(profile.files[0].after, '086ece12aaea13515c77719c6332ca42a955807f2a47f4c196636f8c8af87136');
  for (const args of [[], ['--capture-preroll'], ['--capture-repaint'], ['--legacy-backpressure-v2']]) {
    assert.notEqual(profile.backup, deploymentProfile(args).backup);
  }
  assert.equal(deploymentProfile(['--capture-backpressure', '--rollback']).backup, profile.backup);
  assert.throws(() => deploymentProfile(['--capture-backpressure', '--capture-preroll']), /profile/);
});

test('capture-rate-limit requires the installed q encoder and preserves its own rollback', () => {
  const profile = deploymentProfile(['--capture-rate-limit']);
  assert.equal(profile.root, '/opt/teslaandroid-waydroid');
  assert.deepEqual(profile.files.map(item => item.file), ['runtime/preview-gateway/compositor-h264.py']);
  assert.equal(profile.files[0].before, deploymentProfile(['--capture-backpressure']).files[0].after);
  assert.notEqual(profile.backup, deploymentProfile(['--capture-backpressure']).backup);
  assert.equal(deploymentProfile(['--capture-rate-limit', '--rollback']).backup, profile.backup);
  const source = Buffer.from(require('node:fs').readFileSync(require('node:path').join(__dirname,
    '../tests/fixtures/compositor-h264-live-20261012a.py'), 'utf8').replace(/\r\n/g, '\n'));
  assert.equal(deploymentProfile(['--bitrate-3-runtime']).files[1].before, profile.files[0].after);
  assert.doesNotThrow(() => validateArtifact(source, deploymentProfile(['--bitrate-3-runtime']).files[1].after));
  const portable = Buffer.from(require('node:fs').readFileSync(require('node:path').join(__dirname,
    '../runtime/preview-gateway/compositor-h264.py'), 'utf8').replace(/\r\n/g, '\n'));
  assert.throws(() => validateArtifact(portable, deploymentProfile(['--bitrate-3-runtime']).files[1].after), /checksum/);
  assert.throws(() => deploymentProfile(['--capture-rate-limit', '--capture-backpressure']), /profile/);
});

test('audio-delay profile installs compatible playback before local settings and never touches backend preferences', () => {
  const profile = deploymentProfile(['--audio-delay']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/audio-delay-stage-20261011s');
  assert.deepEqual(profile.files.map(item => item.file), ['js/audio/audioplayback.js', 'js/settings/settings-controller.js']);
  assert.equal(profile.files[0].before, '716ed4a0a0f0144e6fa800f8d3d97233eee256203a4ed7839e0531f00206c63e');
  assert.equal(profile.files[1].before, 'c1514d8ef0233c3cd56689efedce9dd8c0d94bcdc165a957c94eb5a6e259fcad');
  assert.notEqual(profile.backup, deploymentProfile(['--legacy-backpressure-v2']).backup);
  assert.equal(deploymentProfile(['--audio-delay', '--rollback']).backup, profile.backup);
  assert.throws(() => deploymentProfile(['--audio-delay', '--capture-rate-limit']), /profile/);
});

test('audio-delay guard corrects only installed playback and preserves both earlier files and rollback', () => {
  const profile = deploymentProfile(['--audio-delay-guard']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/audio-delay-guard-stage-20261011s6');
  assert.deepEqual(profile.files.map(item => item.file), ['js/audio/audioplayback.js']);
  assert.equal(profile.files[0].before, deploymentProfile(['--audio-delay']).files[0].after);
  assert.equal(profile.files[0].after, '4c18cf01f9d7a1d85b99cb9d2aa326a9ec649e3af57d057db7ec00f548c4e7a7');
  assert.notEqual(profile.backup, deploymentProfile(['--audio-delay']).backup);
  assert.equal(deploymentProfile(['--audio-delay-guard', '--rollback']).backup, profile.backup);
  assert.throws(() => deploymentProfile(['--audio-delay-guard', '--audio-delay']), /profile/);
});

test('Network restoration separates exact runtime and frontend files without targeting preferences or audio', () => {
  const runtime = deploymentProfile(['--network-runtime-restore']);
  const frontend = deploymentProfile(['--network-frontend-restore']);
  assert.equal(runtime.root, '/opt/teslaandroid-waydroid');
  assert.equal(frontend.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(runtime.stage, '/home/hilman/teslaandroid-waydroid/network-restore-stage-20261011t');
  assert.equal(frontend.stage, runtime.stage);
  assert.deepEqual(runtime.files.map(item => item.file), ['runtime/preview-gateway/device-info.js', 'runtime/preview-gateway/server.js']);
  assert.deepEqual(frontend.files.map(item => item.file), ['css/app.css', 'js/settings/settings-controller.js']);
  assert.notEqual(runtime.backup, frontend.backup);
  assert.equal(frontend.files[1].before, deploymentProfile(['--audio-delay']).files[1].after);
  assert.equal(deploymentProfile(['--network-runtime-restore', '--rollback']).backup, runtime.backup);
  assert.equal(deploymentProfile(['--network-frontend-restore', '--rollback']).backup, frontend.backup);
  for (const profile of [runtime, frontend]) {
    assert.notEqual(profile.backup, deploymentProfile(['--audio-delay']).backup);
    assert.notEqual(profile.backup, deploymentProfile(['--audio-delay-guard']).backup);
    assert.equal(profile.files.some(item => /audio|\.json$/.test(item.file)), false);
  }
  assert.throws(() => deploymentProfile(['--network-runtime-restore', '--network-frontend-restore']), /profile/);
});

test('atomic code replacement remains readable under a restrictive owned-job umask', { skip: process.platform !== 'linux' }, () => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ta-deploy-mode-'));
  try {
    // Ownership needs root; only chown is stubbed in the isolated child. Real write, chmod and rename are exercised.
    const script = `const fs=require('node:fs/promises');fs.chown=async()=>{};process.umask(0o077);
      const {replace}=require(process.argv[1]);
      replace(process.argv[2],Buffer.from('generated-code')).then(async()=>{
        console.log((await fs.stat(process.argv[2])).mode&0o777);
      }).catch(()=>{process.exitCode=1});`;
    const child = require('node:child_process').spawnSync(process.execPath, ['-e', script,
      path.join(__dirname, 'deploy-auto-h264-recovery.js'), path.join(directory, 'code.js')], { encoding: 'utf8', timeout: 10000 });
    assert.equal(child.status, 0); assert.equal(Number(child.stdout.trim()), 0o644);
    assert.equal(fs.readFileSync(path.join(directory, 'code.js'), 'utf8'), 'generated-code');
  } finally {
    assert.ok(directory.startsWith(path.resolve(os.tmpdir()) + path.sep));
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('signed A/V sync pins four current frontend files in a separate code-only backup', () => {
  const profile = deploymentProfile(['--av-sync-signed']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/av-sync-signed-stage-20261011v');
  assert.equal(profile.backup, '/var/lib/teslaandroid-waydroid/backups/av-sync-signed-20261011v');
  assert.deepEqual(profile.files.map(item => item.file), [
    'js/audio/audioplayback.js', 'js/display/renderers/h264-broadway/h264Brodway.js',
    'js/display/renderers/h264-webcodecs/h264WebCodecs.js', 'js/settings/settings-controller.js']);
  assert.equal(profile.files[0].before, deploymentProfile(['--audio-delay-guard']).files[0].after);
  assert.equal(profile.files[3].before, deploymentProfile(['--network-frontend-restore']).files[1].after);
  assert.equal(deploymentProfile(['--av-sync-signed', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--av-sync-signed', '--audio-delay']), /profile/);
  assert.equal(profile.files.some(item => /worker|\.json$|runtime\//i.test(item.file)), false);
});

test('settings cleanup fences accepted signed-sync UI and stylesheet only, preserving role APIs and playback', () => {
  const profile = deploymentProfile(['--settings-cleanup']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/settings-cleanup-stage-20261011w');
  assert.equal(profile.backup, '/var/lib/teslaandroid-waydroid/backups/settings-cleanup-20261011w');
  assert.deepEqual(profile.files.map(item => item.file), ['css/app.css', 'js/settings/settings-controller.js']);
  assert.equal(profile.files[1].before, deploymentProfile(['--av-sync-signed']).files[3].after);
  assert.equal(profile.files[0].before, deploymentProfile(['--network-frontend-restore']).files[0].after);
  assert.equal(deploymentProfile(['--settings-cleanup', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--settings-cleanup', '--av-sync-signed']), /profile/);
});

test('unreleased-project update check changes only the exact API client with separate rollback', () => {
  const profile = deploymentProfile(['--project-updates']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.stage, '/home/hilman/teslaandroid-waydroid/project-updates-stage-20261011x');
  assert.equal(profile.backup, '/var/lib/teslaandroid-waydroid/backups/project-updates-20261011x');
  assert.deepEqual(profile.files.map(item => item.file), ['js/core/api-client.js']);
  assert.equal(deploymentProfile(['--project-updates', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--project-updates', '--settings-cleanup']), /profile/);
});

test('DroidDeck branding publishes only its pinned logo and presentation files, with HTML last', () => {
  const profile = deploymentProfile(['--droiddeck-brand']);
  assert.deepEqual(profile.files.map(item => item.file), ['assets/droiddeck-logo.png', 'css/app.css',
    'js/core/shared.js', 'js/shell/shell-controller.js', 'js/audio/audioplayback.js',
    'js/release-notes/release-notes-data.js', 'index.html']);
  assert.equal(profile.files[0].before, null);
  for (const item of profile.files) {
    assert.match(item.after, /^[a-f0-9]{64}$/);
    if (item.before !== null) assert.match(item.before, /^[a-f0-9]{64}$/);
  }
  assert.equal(profile.files[4].before, deploymentProfile(['--av-sync-signed']).files[0].after);
  assert.equal(profile.files[1].before, deploymentProfile(['--settings-cleanup']).files[0].after);
  assert.notEqual(profile.backup, deploymentProfile(['--project-updates']).backup);
  assert.equal(deploymentProfile(['--droiddeck-brand', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--droiddeck-brand', '/tmp/other']), /profile/);
});

test('DroidDeck donation changes only the existing QR and branded HTML with a distinct backup', () => {
  const profile = deploymentProfile(['--droiddeck-donation']);
  assert.deepEqual(profile.files.map(item => item.file), ['assets/donations-qr.svg', 'index.html']);
  assert.equal(profile.files[1].before, deploymentProfile(['--droiddeck-brand']).files[6].after);
  for (const item of profile.files) {
    assert.match(item.before, /^[a-f0-9]{64}$/);
    assert.match(item.after, /^[a-f0-9]{64}$/);
  }
  assert.notEqual(profile.backup, deploymentProfile(['--droiddeck-brand']).backup);
  assert.equal(deploymentProfile(['--droiddeck-donation', '--rollback']).rollback, true);
  assert.throws(() => deploymentProfile(['--droiddeck-donation', '--droiddeck-brand']), /profile/);
  const svg = Buffer.from(require('node:fs').readFileSync(require('node:path').join(__dirname,
    '../runtime/branding/donations-qr.svg'), 'utf8').replace(/\r\n/g, '\n'));
  assert.doesNotThrow(() => validateArtifact(svg, profile.files[0].after));
});

test('3 Mbps deployment separates backend acceptance from the menu and retains exact rollback', () => {
  const runtime = deploymentProfile(['--bitrate-3-runtime']), frontend = deploymentProfile(['--bitrate-3-frontend']);
  assert.equal(runtime.root, '/opt/teslaandroid-waydroid');
  assert.deepEqual(runtime.files.map(item => item.file), ['runtime/preview-gateway/video-settings.js', 'runtime/preview-gateway/compositor-h264.py']);
  assert.deepEqual(frontend.files.map(item => item.file), ['js/settings/settings-controller.js']);
  assert.equal(frontend.files[0].before, deploymentProfile(['--settings-cleanup']).files[1].after);
  assert.notEqual(runtime.backup, frontend.backup);
  for (const args of [['--bitrate-3-runtime'], ['--bitrate-3-frontend']]) {
    const profile = deploymentProfile(args);
    assert.equal(deploymentProfile([...args, '--rollback']).backup, profile.backup);
    assert.throws(() => deploymentProfile([...args, '/tmp/other']), /profile/);
  }
  const data = Buffer.from(require('node:fs').readFileSync(require('node:path').join(__dirname,
    '../runtime/preview-gateway/video-settings.js'), 'utf8').replace(/\r\n/g, '\n'));
  assert.doesNotThrow(() => validateArtifact(data, runtime.files[0].after));
});

test('bitrate downgrade requires stopped writers before reading the saved profile', async () => {
  const { guardBitrateRollback } = require('./deploy-auto-h264-recovery');
  let reads = 0;
  const read = () => { reads++; return { bitRate: 4000000 }; };
  await assert.rejects(guardBitrateRollback(async () => 'active\ninactive\n', read), /Stop streaming runtime/);
  assert.equal(reads, 0);
  await assert.rejects(guardBitrateRollback(async () => 'inactive\nactivating\n', read), /Stop streaming runtime/);
  await assert.rejects(guardBitrateRollback(async () => 'inactive\ninactive\n', () => ({ bitRate: 3000000 })), /legacy-compatible/);
  await guardBitrateRollback(async () => 'inactive\ninactive\n', read);
  assert.equal(reads, 1);
});

test('browser connection installs metadata-only helper before gateway and never targets TLS keys or active hostname state', () => {
  const runtime = deploymentProfile(['--browser-connection-runtime']), frontend = deploymentProfile(['--browser-connection-frontend']);
  assert.deepEqual(runtime.files.map(item => item.file), ['runtime/browser-connection/policy.js',
    'runtime/browser-connection/proxy-store.js', 'runtime/browser-connection/helper.js', 'runtime/preview-gateway/server.js']);
  assert.equal(runtime.files.slice(0, 3).every(item => item.before === null), true);
  assert.deepEqual(frontend.files.map(item => item.file), ['js/core/api-client.js', 'js/settings/settings-controller.js', 'css/app.css']);
  assert.equal(frontend.files[1].before, deploymentProfile(['--bitrate-3-frontend']).files[0].after);
  assert.notEqual(runtime.backup, frontend.backup);
  assert.equal(deploymentProfile(['--browser-connection-runtime', '--rollback']).backup, runtime.backup);
  assert.throws(() => deploymentProfile(['--browser-connection-frontend', '/tmp/other']), /profile/);
  for (const item of runtime.files) {
    const fixture = { 'runtime/browser-connection/helper.js': 'tests/fixtures/browser-connection-helper-live-20261012c.js',
      'runtime/preview-gateway/server.js': 'tests/fixtures/gateway-server-live-20261012c.js' }[item.file] || item.file;
    const source = Buffer.from(require('node:fs').readFileSync(require('node:path').join(__dirname, '..', fixture), 'utf8').replace(/\r\n/g, '\n'));
    assert.doesNotThrow(() => validateArtifact(source, item.after));
    if (fixture !== item.file) {
      const portable = Buffer.from(require('node:fs').readFileSync(require('node:path').join(__dirname, '..', item.file), 'utf8').replace(/\r\n/g, '\n'));
      assert.throws(() => validateArtifact(portable, item.after), /checksum/);
    }
  }
});

test('browser connection patch has portable repository-relative headers and includes mobile styling', () => {
  const patch = require('node:fs').readFileSync(require('node:path').join(__dirname, '../runtime/patches/frontend-browser-connection.patch'), 'utf8');
  const paths = ['js/core/api-client.js', 'js/settings/settings-controller.js', 'css/app.css'];
  assert.deepEqual(patch.split(/\r?\n/).filter(line => line.startsWith('--- ')), paths.map(file => '--- a/' + file));
  assert.deepEqual(patch.split(/\r?\n/).filter(line => line.startsWith('+++ ')), paths.map(file => '+++ b/' + file));
  assert.match(patch, /settings-connection-actions/);
  assert.match(patch, /overflow-wrap: anywhere/);
});

test('settings menu cleanup replaces only the two pinned frontend files, without runtime or preferences', () => {
  const profile = deploymentProfile(['--settings-menu-frontend']);
  assert.deepEqual(profile.files.map(f => f.file), ['css/app.css', 'js/settings/settings-controller.js']);
  assert.equal(profile.root, '/home/hilman/teslaandroid-waydroid/frontend');
  assert.equal(profile.files[0].before, '2596687856957f062eb8a5a3577e038d295abaac83c684d8e72922bef4be37d8');
  assert.equal(profile.files[1].after, 'aab0751ac4906f8295ef162379b5d65f793389faf589a799ce0aa4d9e9d9198a');
  assert.equal(deploymentProfile(['--settings-menu-frontend', '--rollback']).backup, profile.backup);
  assert.throws(() => deploymentProfile(['--settings-menu-frontend', '/override']), /profile/);
});

test('new branding asset refuses replacement and safely rolls back to absence', { skip: process.platform !== 'linux' }, () => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'droiddeck-new-asset-'));
  try {
    // Fake root ownership only in an isolated child; file operations and umask are real.
    const script = `const fs=require('node:fs/promises'),assert=require('node:assert/strict'),crypto=require('node:crypto');
      fs.chown=async()=>{};const stat=fs.lstat;fs.lstat=async p=>{const s=await stat(p);s.uid=0;return s};process.umask(0o077);
      const {assertAbsent,publishNew,restoreAbsent}=require(process.argv[1]);
      const target=process.argv[2],data=Buffer.from('generated-logo'),hash=crypto.createHash('sha256').update(data).digest('hex');
      (async()=>{
        await assertAbsent(target);await publishNew(target,data);
        assert.equal((await fs.stat(target)).mode&0o777,0o644);
        assert.equal((await fs.stat(target)).nlink,1);
        await assert.rejects(assertAbsent(target),/already exists/);
        await assert.rejects(publishNew(target,Buffer.from('other')),e=>e.code==='EEXIST');
        assert.equal((await fs.readFile(target)).toString(),'generated-logo');
        await assert.rejects(restoreAbsent(target,'0'.repeat(64)),/checksum/);
        await restoreAbsent(target,hash);await assertAbsent(target);await restoreAbsent(target,hash);
        await fs.symlink(target+'.missing',target);
        await assert.rejects(assertAbsent(target),/already exists/);
        await assert.rejects(publishNew(target,data),e=>e.code==='EEXIST');
        await assert.rejects(restoreAbsent(target,hash),/Unsafe/);
        assert.equal((await stat(target)).isSymbolicLink(),true);
        await fs.unlink(target);
        await assert.rejects(fs.lstat(target+'.droiddeck-brand-20261011y.tmp'),e=>e.code==='ENOENT');
      })().catch(e=>{console.error(e);process.exitCode=1});`;
    const child = require('node:child_process').spawnSync(process.execPath, ['-e', script,
      path.join(__dirname, 'deploy-auto-h264-recovery.js'), path.join(directory, 'logo.png')], { encoding: 'utf8', timeout: 10000 });
    assert.equal(child.status, 0, child.stderr);
  } finally {
    assert.ok(directory.startsWith(path.resolve(os.tmpdir()) + path.sep));
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
