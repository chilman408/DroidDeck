// SPDX-License-Identifier: GPL-3.0-only
const { createHash } = require('node:crypto');
const { normalizeRuntimeConfig } = require('./runtime-config');
function configurationDigest(configuration) {
  return createHash('sha256').update(JSON.stringify(normalizeRuntimeConfig(configuration))).digest('hex');
}
function assertConfigurationGeneration(configuration, expectedDigest) {
  if (expectedDigest === undefined) return;
  if (typeof expectedDigest !== 'string' || !/^[a-f0-9]{64}$/.test(expectedDigest) ||
      !configuration || configurationDigest(configuration) !== expectedDigest) throw new Error('Runtime configuration generation mismatch');
}
module.exports = { configurationDigest, assertConfigurationGeneration };
