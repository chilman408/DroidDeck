// SPDX-License-Identifier: GPL-3.0-only
const { readPortableConfig } = require('./protected-config');
const { normalizeRuntimeConfig } = require('./runtime-config');
const { validateRuntimeAccount } = require('./runtime-account');
const { assertConfigurationGeneration } = require('./configuration-generation');

async function loadWorkerConfiguration({ role, environment = process.env, uid = process.getuid?.(),
  read = readPortableConfig, validateAccount = validateRuntimeAccount, signal, expectedDigest } = {}) {
  signal?.throwIfAborted();
  if (!['privileged', 'user'].includes(role) || !Number.isSafeInteger(uid) || uid < 0) throw new Error('Invalid worker identity');
  const value = read(environment);
  const configuration = value === undefined ? undefined : normalizeRuntimeConfig(value);
  const expectedUid = role === 'privileged' ? 0 : configuration?.uid ?? 1000;
  if (uid !== expectedUid) throw new Error('Worker identity does not match role');
  assertConfigurationGeneration(configuration, expectedDigest);
  if (configuration) await validateAccount(configuration, { signal });
  signal?.throwIfAborted();
  return configuration;
}
module.exports = { loadWorkerConfiguration };
