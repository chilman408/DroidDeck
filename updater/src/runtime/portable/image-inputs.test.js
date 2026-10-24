// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), { createHash } = require('node:crypto');
const { imageInputPlan, verifyImageInputs } = require('./image-inputs');
async function fixture() {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'droiddeck-image-input-'));
  const plan = structuredClone(imageInputPlan());
  for (const [role, input] of Object.entries(plan.inputs)) {
    const bytes = Buffer.from('generated ' + role + ' fixture'); input.sha256 = createHash('sha256').update(bytes).digest('hex');
    await fs.writeFile(path.join(directory, input.filename), bytes);
  }
  return { directory, plan, close: () => fs.rm(directory, { recursive: true, force: true }) };
}

test('external image plan is frozen publisher metadata and cannot imply redistribution or installation', () => {
  const plan = imageInputPlan(); assert.equal(Object.isFrozen(plan.inputs.system), true);
  assert.equal(plan.installable, false); assert.equal(plan.redistribute, false); assert.equal(plan.android_major, 16);
  assert.equal(plan.inputs.system.sha256, 'f64a746f2ec50417ebb363ad1da1104d5cc71bb39d89ff21a264b5cacaeb006a');
  assert.equal(plan.inputs.vendor.sha256, '28dfbd0e423dff6d5c7d2e34e15011fbaf4865da769d9923b84656d87e9f4d3c');
});

test('streamed image-input receipt validates both fixed archives without reading their contents into memory', async () => {
  const f = await fixture(); try {
    const receipt = await verifyImageInputs(f.directory, { plan: f.plan });
    assert.equal(receipt.installable, false); assert.equal(receipt.extracted, false); assert.equal(receipt.expectedDigestMatched, true);
    assert.equal(receipt.publisherDigestMatched, false, 'Generated fixture pins are not publisher digests');
    assert.equal(receipt.inputs.length, 2); assert.equal(receipt.inputs[0].sha256, f.plan.inputs.system.sha256);
    assert.doesNotMatch(JSON.stringify(receipt), /generated .* fixture/);
    await fs.appendFile(path.join(f.directory, f.plan.inputs.vendor.filename), 'changed');
    await assert.rejects(verifyImageInputs(f.directory, { plan: f.plan }), /digest/);
  } finally { await f.close(); }
});

test('missing, hardlinked, oversized or cancelled inputs and changed policy cannot produce receipts', async () => {
  for (const mutate of [async f => fs.rm(path.join(f.directory, f.plan.inputs.system.filename)),
    async f => fs.link(path.join(f.directory, f.plan.inputs.system.filename), path.join(f.directory, 'duplicate')),
    async f => { f.plan.redistribute = true; }, async f => { f.plan.inputs.system.filename = '../system.zip'; },
    async f => { f.plan.inputs.system.url = 'https://user:secret@other.example/image.zip'; }]) {
    const f = await fixture(); try { await mutate(f); await assert.rejects(verifyImageInputs(f.directory, { plan: f.plan })); } finally { await f.close(); }
  }
  const f = await fixture(); try {
    await assert.rejects(verifyImageInputs(f.directory, { plan: f.plan, maxBytes: 1 }), /input/);
    await assert.rejects(verifyImageInputs(f.directory, { signal: AbortSignal.abort(new Error('cancelled')) }), /cancelled/);
  } finally { await f.close(); }
});

test('archive symlinks are rejected before hashing external files', { skip: process.platform !== 'linux' }, async () => {
  const f = await fixture(); try { const filename = path.join(f.directory, f.plan.inputs.system.filename);
    await fs.rm(filename); await fs.symlink('/etc/passwd', filename);
    await assert.rejects(verifyImageInputs(f.directory, { plan: f.plan }), /input/); } finally { await f.close(); }
});
