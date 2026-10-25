// SPDX-License-Identifier: GPL-3.0-only
const { promisify } = require('node:util'), { execFile } = require('node:child_process');
const { normalizeRuntimeConfig, readRuntimeConfig } = require('./runtime-config');
const { configurationDigest } = require('./configuration-generation');
const { GPU_SERVICE_GROUPS } = require('./service-plan');
const { inspectGpuAccess, parseSupplementalGroups, lookupSupplementalGroups } = require('./gpu-access');
const execute = promisify(execFile);

function parseServiceGpuGroup(value, name) {
  if (!GPU_SERVICE_GROUPS.includes(name)) throw Error('Fixed GPU service group required');
  let text;
  if (Buffer.isBuffer(value) && value.length <= 4096) text = new TextDecoder('utf-8', { fatal: true }).decode(value);
  else if (typeof value === 'string' && Buffer.byteLength(value) <= 4096) text = value;
  else throw Error('Invalid GPU service group mapping');
  text = text.replace(/\r?\n$/, '');
  const fields = text.split(':');
  if (fields.length !== 4 || fields[0] !== name || /[\x00-\x1f\x7f]/.test(text) ||
      !/^[1-9]\d{0,9}$/.test(fields[2]) || Number(fields[2]) > 2147483647) throw Error('Invalid GPU service group mapping');
  return Object.freeze({ name, gid: Number(fields[2]) });
}
async function lookupServiceGroup(name, { signal } = {}) {
  if (process.platform !== 'linux' || !GPU_SERVICE_GROUPS.includes(name)) throw Error('Fixed Linux group lookup required');
  const result = await execute('/usr/bin/getent', ['group', name],
    { signal, timeout: 5000, maxBuffer: 4096, encoding: 'buffer', windowsHide: true });
  return result.stdout;
}
async function inspectGpuServiceAccess(configuration, { lookupGroup = lookupServiceGroup,
  lookupGroups = lookupSupplementalGroups, io, lookup, signal } = {}) {
  signal?.throwIfAborted();
  const config = normalizeRuntimeConfig(configuration);
  async function mappings() {
    const values = [];
    for (const name of GPU_SERVICE_GROUPS) {
      signal?.throwIfAborted();
      const row = await lookupGroup(name, { signal });
      signal?.throwIfAborted(); values.push(parseServiceGpuGroup(row, name));
    }
    if (new Set(values.map(value => value.gid)).size !== values.length) throw Error('Aliased GPU service groups');
    return Object.freeze(values);
  }
  const serviceSupplementaryGroups = await mappings();
  let accountGroups;
  const result = await inspectGpuAccess(config, { io, lookup, signal,
    lookupGroups: async (user, options) => {
      const groups = parseSupplementalGroups(await lookupGroups(user, options), config.gid);
      signal?.throwIfAborted();
      // A membership change must not be hidden by the service's added groups.
      if (accountGroups && JSON.stringify(groups) !== JSON.stringify(accountGroups)) throw Error('GPU account groups changed');
      accountGroups = groups;
      return [...new Set([...groups, ...serviceSupplementaryGroups.map(value => value.gid)])].sort((a, b) => a - b).join(' ');
    } });
  if (JSON.stringify(serviceSupplementaryGroups) !== JSON.stringify(await mappings())) throw Error('GPU service mapping changed');
  signal?.throwIfAborted();
  const renderGroupMatchesDevice = result.nodes.some(node => node.kind === 'render' && node.gid === serviceSupplementaryGroups[0].gid);
  const videoGroupMatchesDevice = result.nodes.some(node => node.kind === 'card' && node.gid === serviceSupplementaryGroups[1].gid);
  return Object.freeze({ ...result, kind: 'gpu-service-access-preflight', configurationDigest: configurationDigest(config),
    credentialBasis: 'proposed-system-service', accountGroups, serviceSupplementaryGroups,
    renderGroupMatchesDevice, videoGroupMatchesDevice,
    proposedServiceGpuModeAccess: renderGroupMatchesDevice && videoGroupMatchesDevice &&
      result.unattendedRenderModeAccess && result.unattendedCardModeAccess,
    effectiveServicePolicyVerified: false, actualServiceCredentialsVerified: false,
    accountMembershipChanged: false, servicesActivated: false, androidOperationsExecuted: false, readyForActivation: false });
}
async function main(args) {
  if (process.platform !== 'linux' || args.length !== 2 || args[0] !== 'inspect') throw Error('Fixed Linux GPU service preflight required');
  console.log(JSON.stringify(await inspectGpuServiceAccess(readRuntimeConfig(args[1])), null, 2));
}
if (require.main === module) main(process.argv.slice(2)).catch(() => { console.error('GPU-service preflight rejected'); process.exitCode = 1; });
module.exports = { parseServiceGpuGroup, inspectGpuServiceAccess };
