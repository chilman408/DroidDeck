// SPDX-License-Identifier: GPL-3.0-only
const { domainToASCII } = require('node:url');
const { isIP } = require('node:net');
const DEFAULT_HOSTNAME = 'device.teslaandroid.com';
const DEFAULT_ORIGIN = 'https://' + DEFAULT_HOSTNAME;
function normalizeHostname(value) {
  if (typeof value !== 'string' || value.length > 256 || /[\s/:?#@\\*]/u.test(value) || value.endsWith('.')) {
    throw new Error('Invalid hostname');
  }
  const hostname = domainToASCII(value).toLowerCase();
  const labels = hostname.split('.');
  if (!hostname || hostname.length > 90 || isIP(hostname) || labels.length < 2 || /^\d+$/.test(labels.at(-1)) ||
      labels.some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) {
    throw new Error('Invalid hostname');
  }
  return hostname;
}
function normalizeProfile(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some(key => !['kind', 'hostname'].includes(key)) ||
      !['default', 'reverse-proxy', 'manual', 'duckdns'].includes(value.kind)) throw new Error('Invalid connection profile');
  const hostname = normalizeHostname(value.hostname ?? (value.kind === 'default' ? DEFAULT_HOSTNAME : ''));
  if (value.kind === 'default' && hostname !== DEFAULT_HOSTNAME) throw new Error('Invalid default hostname');
  if (value.kind === 'duckdns' && !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.duckdns\.org$/.test(hostname)) {
    throw new Error('Invalid DuckDNS hostname');
  }
  return { version: 1, kind: value.kind, hostname, origin: 'https://' + hostname,
    certificateManagement: { default: 'local-admin', 'reverse-proxy': 'reverse-proxy', manual: 'manual-import', duckdns: 'automatic-dns01' }[value.kind],
    trustVerified: false };
}
module.exports = { DEFAULT_HOSTNAME, DEFAULT_ORIGIN, normalizeHostname, normalizeProfile };
