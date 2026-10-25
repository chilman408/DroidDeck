// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { createGateway } = require('./server');
test('update actions require exact path, origin, current owner CSRF and closed bounded JSON before helper', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dd-update-api-')); fs.writeFileSync(path.join(dir, 'index.html'), 'test');
  const requests = [];
  const displayControl = { on() {}, off() {}, close() {}, removeListener() {}, snapshot(token) { if(token!=='session') throw Object.assign(Error('session'),{status:403});return {}; },
    assertOwner(token,csrf) { if(token!=='session'||csrf!=='csrf')throw Object.assign(Error('owner'),{status:403}); } };
  const server = createGateway({ frontendDir:dir, displayControl, updateControl: { async request(value) { requests.push(value); return {protocol:1,installedVersion:'0.1.1-rc.8',available:null,job:null,rollback:null}; } } });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  t.after(()=>{server.closeAllConnections();server.close();fs.rmSync(dir,{recursive:true,force:true});});
  const origin='http://127.0.0.1:'+server.address().port;
  const send=(payload,headers={},url='/api/updates')=>fetch(origin+url,{method:'POST',headers:{origin,'content-type':'application/json','x-display-session':'session','x-display-csrf':'csrf',...headers},body:JSON.stringify(payload)});
  for(const [payload,headers,url,status] of [
    [{action:'check'},{origin:'https://evil.invalid'},undefined,403],
    [{action:'install',token:'a'.repeat(32),requestId:'b'.repeat(32)},{'x-display-csrf':'wrong'},undefined,403],
    [{action:'check',url:'file:///etc/passwd'},{},undefined,400],
    [{action:'check'},{},'/api/%75pdates',400],
    [{action:'check'},{},'/api/updates?url=foo',400],
    [{action:'check',extra:'x'.repeat(600)},{},undefined,413]]) {
      assert.equal((await send(payload,headers,url)).status,status);
    }
  assert.deepEqual(requests,[]);
  assert.equal((await send({action:'check'})).status,200);
  assert.deepEqual(requests,[{action:'check'}]);
  assert.equal((await fetch(origin+'/api/updates')).status,403);
  assert.equal((await fetch(origin+'/api/updates',{headers:{'x-display-session':'session'}})).status,200);
  assert.deepEqual(requests.at(-1),{action:'status'});
});
