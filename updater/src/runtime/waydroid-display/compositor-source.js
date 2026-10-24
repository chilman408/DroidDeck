// SPDX-License-Identifier: GPL-3.0-only
const { displayViewport } = require('./touch-adapter');
function compositorNode(objects, pid) {
  if (!Array.isArray(objects) || objects.length > 4096 || !Number.isSafeInteger(pid) || pid < 2) throw new Error('Invalid PipeWire snapshot');
  const clients = new Set(objects.filter(object => object?.type === 'PipeWire:Interface:Client' &&
    Number(object.info?.props?.['application.process.id']) === pid).map(object => object.id));
  const matches = objects.filter(object => object?.type === 'PipeWire:Interface:Node' &&
    ['Video/Source', 'Stream/Output/Video'].includes(object.info?.props?.['media.class']) &&
    (Number(object.info.props['application.process.id']) === pid || clients.has(object.info.props['client.id'])) &&
    typeof object.info.props['node.name'] === 'string' && object.info.props['node.name'].startsWith('weston.'));
  const serial = matches[0]?.info?.props?.['object.serial'];
  if (matches.length !== 1 || !Number.isInteger(matches[0].id) || matches[0].id < 1 || matches[0].id > 2147483647 ||
      !Number.isSafeInteger(serial) || serial < 1) throw new Error('Isolated Weston pattern node unavailable or ambiguous');
  return { id: matches[0].id, serial };
}
function capturePlan(inputDump, size, node) {
  const viewport = displayViewport(inputDump, size);
  if (!Number.isSafeInteger(node?.serial) || node.serial < 1 ||
      viewport.device.some(value => value < 96 || value > 1920) || viewport.device[0] * viewport.device[1] > 1600000) {
    throw new Error('Unsupported compositor capture geometry');
  }
  const [left, top, endX, endY] = viewport.physical;
  return { serial: node.serial, width: size.width, height: size.height,
    sourceWidth: viewport.device[0], sourceHeight: viewport.device[1],
    left, top, right: viewport.device[0] - endX, bottom: viewport.device[1] - endY };
}
module.exports = { compositorNode, capturePlan };
