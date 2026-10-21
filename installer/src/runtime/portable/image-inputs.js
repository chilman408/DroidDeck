// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), io = require('node:fs/promises'), path = require('node:path');
const { createHash } = require('node:crypto'), { isDeepStrictEqual } = require('node:util');
const source = require('../../config/portable-image-inputs.json');
const reference = Object.freeze({ ...source, inputs: Object.freeze(Object.fromEntries(
  Object.entries(source.inputs).map(([role, input]) => [role, Object.freeze({ ...input })]))) });
const MAX_ARCHIVE = 8 * 1024 * 1024 * 1024;
function normalizePlan(value) {
  if (!value || !isDeepStrictEqual(Object.keys(value).sort(), Object.keys(reference).sort()) ||
      Object.entries(reference).some(([key, item]) => key !== 'inputs' && value[key] !== item) ||
      !value.inputs || !isDeepStrictEqual(Object.keys(value.inputs).sort(), ['system', 'vendor'])) throw new Error('Image input policy rejected');
  const inputs = {};
  for (const role of ['system', 'vendor']) {
    const input = value.inputs[role], original = reference.inputs[role];
    if (!input || !isDeepStrictEqual(Object.keys(input).sort(), Object.keys(original).sort()) ||
        ['filename', 'url', 'member'].some(key => input[key] !== original[key]) ||
        typeof input.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(input.sha256)) throw new Error('Image input source rejected');
    inputs[role] = Object.freeze({ ...input });
  }
  return Object.freeze({ ...value, inputs: Object.freeze(inputs) });
}
function imageInputPlan() { return normalizePlan(reference); }
function safeFile(stat, bound) {
  if (!stat.isFile() || stat.nlink !== 1 || !Number.isSafeInteger(stat.size) || stat.size < 1 || stat.size > bound) throw new Error('Unsafe or oversized image input');
}
function sameFile(a, b) {
  return ['ino', 'dev', 'size', 'mtimeMs', 'ctimeMs', 'nlink'].every(key => a[key] === b[key]);
}
async function hashArchive(filename, bound, signal) {
  signal?.throwIfAborted();
  const before = await io.lstat(filename); safeFile(before, bound);
  const handle = await io.open(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const opened = await handle.stat(); safeFile(opened, bound);
    if (!sameFile(before, opened)) throw new Error('Image input changed');
    const hash = createHash('sha256'); let bytes = 0;
    const stream = handle.createReadStream({ autoClose: false, start: 0, end: opened.size, signal });
    for await (const chunk of stream) { signal?.throwIfAborted(); bytes += chunk.length; hash.update(chunk); }
    const after = await handle.stat(), current = await io.lstat(filename); safeFile(after, bound); safeFile(current, bound);
    if (bytes !== opened.size || !sameFile(opened, after) || !sameFile(opened, current)) throw new Error('Image input changed');
    return { size: bytes, sha256: hash.digest('hex') };
  } finally { await handle.close(); }
}
async function verifyImageInputs(directory, { plan = imageInputPlan(), maxBytes = MAX_ARCHIVE, signal } = {}) {
  signal?.throwIfAborted(); plan = normalizePlan(plan);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > MAX_ARCHIVE || !(await io.lstat(directory)).isDirectory()) throw new Error('Invalid image input stage');
  const inputs = [];
  for (const [role, input] of Object.entries(plan.inputs)) {
    const observed = await hashArchive(path.join(directory, input.filename), maxBytes, signal);
    if (observed.sha256 !== input.sha256) throw new Error('Image input digest mismatch');
    inputs.push(Object.freeze({ role, filename: input.filename, member: input.member, ...observed }));
  }
  return Object.freeze({ kind: 'external-image-receipt', installable: false, redistribute: false, downloaded: false, extracted: false,
    release: plan.release, expectedDigestMatched: true,
    publisherDigestMatched: inputs.every(input => input.sha256 === reference.inputs[input.role].sha256), inputs: Object.freeze(inputs) });
}
if (require.main === module) {
  (async () => {
    if (process.argv.length !== 4 || process.argv[2] !== 'verify') throw new Error('Use verify STAGE');
    console.log(JSON.stringify(await verifyImageInputs(process.argv[3]), null, 2));
  })().catch(() => { console.error('External image inputs rejected'); process.exitCode = 1; });
}
module.exports = { imageInputPlan, verifyImageInputs };
