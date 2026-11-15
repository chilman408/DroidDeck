// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), c = require('node:crypto');
const { operation, canon, sign, signCaptureActivation, signCaptureCandidate } = require('./release-signer-crypto');
const pair=c.generateKeyPairSync('ed25519'),raw=pair.privateKey.export({format:'der',type:'pkcs8'});
const pub=pair.publicKey.export({format:'der',type:'spki'}),sha=b=>c.createHash('sha256').update(b).digest('hex');
const value={schema:1,kind:'droidmate-aggregate-release-authorization',channel:'pilot',aggregateRelease:'RC17',
 coreRuntimeVersion:'0.1.1-rc.20',compatibleReleaseSha256:'1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a',
 activationAllowed:true,keySha256:sha(pub),packageSha256:'a'.repeat(64),manifestSha256:'b'.repeat(64),sourceRevision:'c'.repeat(40),
 sourceInventorySha256:'d'.repeat(64),contractSha256:'40baefbcecf7d9b95b4457d283bd6951bbe90fc8da4926eb680778ce4721d394'};
const statement=Buffer.from(canon(value));
function request(s=statement){return {mode:'signAggregate',private:raw.toString('base64'),statement:s.toString('base64'),expectedStatementSha256:sha(s)};}
test('separate aggregate fixture signature is real Ed25519 and exports no private key',()=>{
 const result=operation(request()),signature=Buffer.from(result.signature,'base64');
 assert.equal(result.aggregateAuthorizationSigned,true);assert.equal(result.servicesChanged,false);
 assert.equal(result.keySha256,sha(pub));assert.equal(signature.length,64);assert.equal(c.verify(null,statement,pair.publicKey,signature),true);
 assert.equal(c.verify(null,Buffer.concat([statement,Buffer.from(' ')]),pair.publicKey,signature),false);
 assert.equal(Object.hasOwn(result,'private'),false);
});
test('aggregate rejects other domain/core/contract/key/schema/extra fields and noncanonical bytes',()=>{
 for(const change of [{kind:'droiddeck-capture-activation-authorization'},{aggregateRelease:'RC16'},{channel:'stable'},
  {coreRuntimeVersion:'0.1.1-rc.17'},{compatibleReleaseSha256:'e'.repeat(64)},{activationAllowed:false},{keySha256:'f'.repeat(64)},
  {contractSha256:'0'.repeat(64)},{schema:true},{extra:true},{sourceRevision:'A'.repeat(40)},{packageSha256:null}]){
  assert.throws(()=>operation(request(Buffer.from(canon({...value,...change})))));
 }
 for(const s of [statement.subarray(0,-1),Buffer.from(JSON.stringify(value)),
  Buffer.from(statement.toString().replace('"schema":1','"schema":1,"schema":1'))])assert.throws(()=>operation(request(s)));
 assert.throws(()=>operation({...request(),expectedStatementSha256:'f'.repeat(64)}));
});
test('existing release and capture signing domains reject aggregate statements, existing public verification still works',()=>{
 for(const fn of [sign,signCaptureActivation,signCaptureCandidate])assert.throws(()=>fn(raw,statement,sha(statement)));
 const result=operation({mode:'verify',private:raw.toString('base64')});assert.equal(result.keySha256,sha(pub));assert.equal(result.selfTest,true);
});


test('RC18 successor signing is separate from the immutable RC17 aggregate domain',()=>{
 const s=Buffer.from(canon({...value,aggregateRelease:'RC18'}));
 const result=operation({...request(s),mode:'signSuccessor'});
 assert.equal(c.verify(null,s,pair.publicKey,Buffer.from(result.signature,'base64')),true);
 assert.equal(Object.hasOwn(result,'private'),false);
 assert.throws(()=>operation(request(s)));
 assert.throws(()=>operation({...request(),mode:'signSuccessor'}));
 for(const patch of [{aggregateRelease:'RC19'},{contractSha256:'0'.repeat(64)},{activationAllowed:false},{extra:1}]){
  const bytes=Buffer.from(canon({...value,aggregateRelease:'RC18',...patch}));assert.throws(()=>operation({...request(bytes),mode:'signSuccessor'}));
 }
});

test.after(()=>raw.fill(0));
