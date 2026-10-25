// SPDX-License-Identifier: GPL-3.0-only
// Diagnostics/backports use the exact production pure helper, with no drift.
const {normalizeScrcpyPacket}=require('../runtime/preview-gateway/webcodecs-worker-policy');
function workerPolicySource({diagnostics=true}={}) {
  return `(()=>{const normalize=${normalizeScrcpyPacket.toString()};
const original=handleScrcpyPacket;
handleScrcpyPacket=function(packet){const result=normalize(packet);
${diagnostics?'if(result.audRemoved)self.postMessage({webCodecsAudRemoved:result.audRemoved});':''}
return original(result.packet);};})();`;
}
module.exports={normalizeScrcpyPacket,workerPolicySource};
