// SPDX-License-Identifier: GPL-3.0-only
const { normalizeHostname, DEFAULT_HOSTNAME } = require('../browser-connection/policy');
const { readBoundedRegular } = require('./read-bounded');
const FIELDS = ['version', 'user', 'uid', 'gid', 'home', 'lanAddress', 'port', 'hostname'];
function integer(value, minimum, maximum) {
  return Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}
function privateAddress(value) {
  if (typeof value !== 'string' || !/^(?:0|[1-9]\d{0,2})(?:\.(?:0|[1-9]\d{0,2})){3}$/.test(value)) return false;
  const octets = value.split('.').map(Number);
  return octets.every(part => part <= 255) && octets[3] > 0 && octets[3] < 255 &&
    (octets[0] === 10 || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) || (octets[0] === 192 && octets[1] === 168));
}
function normalizeRuntimeConfig(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !FIELDS.includes(key))) throw new Error('Invalid runtime configuration schema');
  for (const key of ['user', 'uid', 'gid', 'lanAddress']) if (!Object.hasOwn(value, key)) throw new Error('Missing runtime configuration field');
  const version = Object.hasOwn(value, 'version') ? value.version : 1;
  const { user, uid, gid, lanAddress } = value;
  if (version !== 1 || typeof user !== 'string' || !/^[a-z_][a-z0-9_-]{0,31}$/.test(user) || user === 'root' ||
      !integer(uid, 1000, 2147483647) || !integer(gid, 1000, 2147483647)) throw new Error('Invalid runtime identity');
  const home = Object.hasOwn(value, 'home') ? value.home : '/home/' + user;
  if (typeof home !== 'string' || !/^\/home\/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(home) || home.endsWith('.') ||
      /^\/home\/(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(home)) throw new Error('Invalid runtime home');
  const port = Object.hasOwn(value, 'port') ? value.port : 8443;
  if (!integer(port, 1024, 65535) || !privateAddress(lanAddress)) throw new Error('Invalid runtime LAN endpoint');
  const hostname = normalizeHostname(Object.hasOwn(value, 'hostname') ? value.hostname : DEFAULT_HOSTNAME);
  return Object.freeze({ version, user, uid, gid, lanAddress, home, port, hostname });
}
function deriveRuntimeLayout(input) {
  const config = normalizeRuntimeConfig(input), root = '/opt/droiddeck', preferences = config.home + '/.config/droiddeck';
  const runtimeDirectory = '/run/user/' + config.uid;
  return Object.freeze({ root, state: '/var/lib/droiddeck', sockets: '/run/droiddeck',
    frontend: root + '/frontend', hostLibraries: root + '/libgbinder', preferences,
    runtimeDirectory, dbusAddress: 'unix:path=' + runtimeDirectory + '/bus',
    keyFile: preferences + '/key.pem', certificateFile: preferences + '/cert.pem',
    gatewayOrigin: 'https://' + config.lanAddress + ':' + config.port, publicOrigin: 'https://' + config.hostname });
}
function readRuntimeConfig(filename) {
  try {
    const data = readBoundedRegular(filename, 16384);
    return normalizeRuntimeConfig(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data)));
  } catch (_) { throw new Error('Runtime configuration rejected'); }
}
function main(args) {
  if (args.length !== 2 || args[0] !== 'inspect') throw new Error('Use inspect FILE');
  const config = readRuntimeConfig(args[1]); console.log(JSON.stringify({ config, layout: deriveRuntimeLayout(config), installable: false }, null, 2));
}
if (require.main === module) { try { main(process.argv.slice(2)); } catch (_) { console.error('Runtime configuration rejected'); process.exitCode = 1; } }
module.exports = { normalizeRuntimeConfig, deriveRuntimeLayout, readRuntimeConfig };
