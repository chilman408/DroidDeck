// SPDX-License-Identifier: GPL-3.0-only
// Root launches disposable children; only each child's credentials are changed.
const assert = require('node:assert/strict'), path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');
const { readRuntimeConfig } = require('./runtime-config');
const { parseServiceGpuGroup } = require('./gpu-service-access');
const { parseSupplementalGroups } = require('./gpu-access');
const child = `
const assert = require('node:assert/strict');
const { assertGpuProcessAccess } = require(process.argv[1]);
const config = JSON.parse(process.argv[2]), groups = JSON.parse(process.argv[3]);
process.setgroups(groups); process.setgid(config.gid); process.setuid(config.uid);
assertGpuProcessAccess(config).then(result => {
  assert.equal(groups.length, 2); assert.equal(result.actualProcessGpuModeAccess, true);
  assert.equal(result.processCredentialsMatched, true); assert.equal(result.deviceAccessTested, false);
  assert.equal(result.actualServiceMainProcessVerified, false); assert.equal(result.installable, false);
  assert.equal(result.servicesActivated, false); assert.equal(result.gpuAccelerationVerified, false);
  console.log(JSON.stringify({ actualUnprivilegedProcess: true, modeAccess: true }));
}).catch(error => {
  if (groups.length !== 0 || error.message !== 'GPU process lacks DRM mode access') process.exitCode = 1;
  else console.log(JSON.stringify({ actualUnprivilegedProcess: true, missingGroupsRefused: true }));
});`;
function main(args) {
  if (process.platform !== 'linux' || process.getuid() !== 0 || process.geteuid() !== 0 ||
      args.length !== 1 || args[0] !== '--proof') throw Error('Fixed root Linux proof required');
  const config = readRuntimeConfig('/etc/droiddeck/runtime.json');
  const env = { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8' };
  const options = { encoding: 'utf8', timeout: 10000, maxBuffer: 4096, env, cwd: '/' };
  const memberships = () => parseSupplementalGroups(execFileSync('/usr/bin/id', ['-G', '--', config.user], options), config.gid);
  const before = memberships(), parent = [process.getuid(), process.getgid(), process.getgroups()];
  const gids = ['render', 'video'].map(name => parseServiceGpuGroup(execFileSync('/usr/bin/getent', ['group', name], options), name).gid);
  assert.notEqual(gids[0], gids[1]);
  const outcomes = [gids, []].map(groups => {
    const result = spawnSync(process.execPath, ['-e', child, path.join(__dirname, 'gpu-access.js'),
      JSON.stringify(config), JSON.stringify(groups)], options);
    assert.equal(result.error, undefined); assert.equal(result.status, 0); assert.equal(result.stderr, '');
    return JSON.parse(result.stdout);
  });
  const root = spawnSync(process.execPath, [path.join(__dirname, 'gpu-access.js'), 'inspect-self', '/etc/droiddeck/runtime.json'], options);
  assert.equal(root.error, undefined); assert.equal(root.status, 1); assert.equal(root.stdout, '');
  assert.equal(root.stderr.trim(), 'GPU-access preflight rejected');
  assert.deepEqual(memberships(), before); assert.deepEqual([process.getuid(), process.getgid(), process.getgroups()], parent);
  console.log(JSON.stringify({ kind: 'generated-gpu-process-proof', outcomes, rootRefused: true,
    accountMembershipUnchanged: true, parentCredentialsUnchanged: true, servicesActivated: false,
    deviceAccessTested: false, gpuAccelerationVerified: false, readyForActivation: false }));
}
if (require.main === module) {
  try { main(process.argv.slice(2)); } catch (_) { console.error('GPU process proof rejected'); process.exitCode = 1; }
}
