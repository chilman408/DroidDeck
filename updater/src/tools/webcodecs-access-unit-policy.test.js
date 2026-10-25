// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
function packet(payload,flags=0x20) {
  const b=new Uint8Array(12+payload.length);b[0]=flags;b[3]=7;b[7]=99;
  new DataView(b.buffer).setUint32(8,payload.length);b.set(payload,12);return b.buffer;
}
test('only AUD NALs are removed; multi-slice picture and packet metadata are byte-exact',()=>{
  const {normalizeScrcpyPacket}=require('./webcodecs-access-unit-policy');
  const aud=[0,0,0,1,9,240], sps=[0,0,0,1,103,66,192,31,128],pps=[0,0,1,104,128],
    sei=[0,0,1,6,1,128],idr1=[0,0,1,101,23,0,0,3,1,45],idr2=[0,0,0,1,101,67,89];
  const input=packet([...aud,...sps,...pps,...sei,...idr1,...idr2,...aud]);
  const result=normalizeScrcpyPacket(input);
  assert.equal(result.audRemoved,2);
  assert.deepEqual(new Uint8Array(result.packet),new Uint8Array(packet([...sps,...pps,...sei,...idr1,...idr2])));
  assert.notEqual(result.packet,input);
  assert.equal(new Uint8Array(input)[16],9);
});
test('unchanged and malformed inputs retain original ownership and bounded validation',()=>{
  const {normalizeScrcpyPacket}=require('./webcodecs-access-unit-policy');
  const cases=[packet([0,0,1,101,128]),packet([0,0,1,103,66,192,31,128],0x40),new ArrayBuffer(12),new ArrayBuffer(2097165),new Uint8Array(20)];
  const bad=packet([0,0,1,9,240]);new DataView(bad).setUint32(8,999);cases.push(bad);
  cases.push(packet(Array.from({length:4097},()=>[0,0,1,9,240]).flat()));
  for(const input of cases){const r=normalizeScrcpyPacket(input);assert.equal(r.packet,input);assert.equal(r.audRemoved,0);}
});
test('worker policy passes one complete normalized AU to original handler and reports only count',()=>{
  const {workerPolicySource}=require('./webcodecs-access-unit-policy');
  const calls=[],messages=[];const context=vm.createContext({ArrayBuffer,Uint8Array,DataView,
    handleScrcpyPacket:b=>calls.push(b),self:{postMessage:m=>messages.push(m)}});
  vm.runInContext(workerPolicySource(),context);
  const b=packet([0,0,1,9,240,0,0,1,101,128]);context.handleScrcpyPacket(b);
  assert.equal(calls.length,1);assert.deepEqual(new Uint8Array(calls[0]),new Uint8Array(packet([0,0,1,101,128])));
  assert.equal(JSON.stringify(messages),'[{"webCodecsAudRemoved":1}]');
});
test('invalid NAL headers or framing are passed unchanged to original validation',()=>{
  const {normalizeScrcpyPacket}=require('./webcodecs-access-unit-policy');
  for(const payload of [
    [0,0,1,0x89,240,0,0,1,101,128],
    [0,0,1,9,240,0,0,1,0xe5,128],
    [0,0,1,0x29,240,0,0,1,101,128],
    [55,0,0,1,9,240,0,0,1,101,128],
    [0,0,1,9,240,0,0,1],
  ]) {
    const input=packet(payload),result=normalizeScrcpyPacket(input);
    assert.equal(result.packet,input);assert.equal(result.audRemoved,0);
  }
});
