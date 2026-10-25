// SPDX-License-Identifier: GPL-3.0-only
const net = require('node:net');
const SOCKET = '/run/droiddeck-update-ui/control.sock';
function createUpdateClient(filename = SOCKET) {
  return { request(payload) { return new Promise((resolve, reject) => {
    let body = '', done = false;
    const socket = net.connect(filename);
    const finish = (error, value) => { if (done) return; done = true; clearTimeout(timer); socket.destroy(); error ? reject(error) : resolve(value); };
    const timer = setTimeout(() => finish(new Error('Update helper timed out')), 35000);
    socket.on('error', () => finish(new Error('Update helper unavailable')));
    socket.on('connect', () => socket.end(JSON.stringify(payload) + '\n'));
    socket.on('data', chunk => { body += chunk.toString('utf8'); if (Buffer.byteLength(body) > 8192) finish(new Error('Bounded update response required')); });
    socket.on('end', () => { try {
      const response = JSON.parse(body);
      if (response.error) throw new Error('Update helper refused request');
      const state = response.state;
      if (!state || state.protocol !== 1 || typeof state.installedVersion !== 'string' ||
          !Object.hasOwn(state, 'job') || !Object.hasOwn(state, 'available')) throw new Error('Update protocol unavailable');
      finish(null, state);
    } catch (error) { finish(error); } });
    socket.on('close', () => { if (!done) finish(new Error('Update helper disconnected')); });
  }); } };
}
module.exports = { createUpdateClient, SOCKET };
