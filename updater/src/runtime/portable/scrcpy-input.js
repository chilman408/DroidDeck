// SPDX-License-Identifier: GPL-3.0-only
const fs = require('node:fs'), path = require('node:path'), { createHash } = require('node:crypto');
const { readBoundedRegular } = require('./read-bounded');
const plan = Object.freeze({ version: '4.0', assetId: 418648068, filename: 'scrcpy-server-v4.0', runtimeFilename: 'scrcpy-server-v4.0.jar',
  url: 'https://github.com/Genymobile/scrcpy/releases/download/v4.0/scrcpy-server-v4.0', size: 732226,
  sha256: '84924bd564a1eb6089c872c7521f968058977f91f5ff02514a8c74aff3210f3a' });
function scrcpyInputPlan() { return plan; }
function parents(filename) {
  const directory = path.dirname(filename); let current = path.parse(directory).root;
  for (const part of directory.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Linked input ancestry');
  }
}
function verifyScrcpyInput(filename) {
  if (typeof filename !== 'string' || filename.length > 4096 || !path.isAbsolute(filename) || path.resolve(filename) !== filename) throw new Error('Canonical absolute input required');
  parents(filename); const before = fs.lstatSync(filename);
  if (!before.isFile() || before.nlink !== 1 || before.size !== plan.size) throw new Error('Invalid publisher input');
  const bytes = readBoundedRegular(filename, plan.size), after = fs.lstatSync(filename); parents(filename);
  if (!after.isFile() || !['ino', 'dev', 'size', 'mtimeMs', 'ctimeMs', 'nlink'].every(key => before[key] === after[key])) throw new Error('Input generation changed');
  if (bytes.length !== plan.size || !bytes.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 3, 4])) ||
      createHash('sha256').update(bytes).digest('hex') !== plan.sha256) throw new Error('Publisher pin mismatch');
  return Object.freeze({ kind: 'external-scrcpy-server-receipt', ...plan, publisherPinMatched: true,
    installable: false, installed: false, downloaded: false, redistributed: false,
    androidOperationsExecuted: false, signatureVerified: false, zipFormatVerified: false, zipMagicChecked: true });
}
if (require.main === module) {
  try {
    const args = process.argv.slice(2); if (args.length !== 2 || args[0] !== 'verify') throw new Error('Invalid invocation');
    process.stdout.write(JSON.stringify(verifyScrcpyInput(args[1])) + '\n');
  } catch { process.stderr.write('External scrcpy input rejected\n'); process.exitCode = 1; }
}
module.exports = { scrcpyInputPlan, verifyScrcpyInput };
