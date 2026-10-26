// SPDX-License-Identifier: GPL-3.0-only
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require.resolve('./browser-adaptive-policy'), 'utf8');
function fixture() {
  const nodes = [], requests = [];
  const node = tag => ({ tag, dataset: {}, children: [], isConnected: true, append(...v) { this.children.push(...v); }, appendChild(v) { this.children.push(v); }, setAttribute(k,v) { this[k]=v; } });
  const base = { async _renderDisplaySection() {}, refreshDisplayControl(s) { this.displayController.remoteDisplayState=s; } };
  const window = { TAHtml: { SettingsController: function() {} }, document: { createElement: node, createTextNode: text => ({textContent:text}) },
    AbortSignal: { timeout: () => null }, async fetch(url, options) { requests.push({url,options}); return {ok:true,json:async()=>({policy:{enabled:false,revision:2,ceilingBitRate:4000000,ceilingMaxFps:60},profile:{revision:4,bitRate:3000000,maxFps:45}})}; } };
  window.TAHtml.SettingsController.prototype=base; vm.runInNewContext(source,{window});
  const controller=Object.create(base);
  Object.assign(controller,{_renderToken:1,activeSectionId:'display',apiClient:{displaySession:{token:'session',csrfToken:'csrf'}},displayController:{remoteDisplayState:{canControl:false,transactionStatus:'idle'}},
    content:{querySelector(selector){return selector.includes('video-bitrate')?{}:nodes[0]?.children[0]?.children[0];},appendChild(n){nodes.push(n);}},async _renderActiveSection(){this.rendered=true;}});
  return {controller,nodes,requests};
}
test('Automatic quality follows primary ownership and sends only closed opt-in state with existing display credentials',async()=>{
 const f=fixture();await f.controller._renderDisplaySection(1);const input=f.nodes[0].children[0].children[0];
 assert.equal(input.disabled,true);input.checked=true;await input.onchange();assert.equal(f.requests.length,1);
 f.controller.refreshDisplayControl({canControl:true,transactionStatus:'idle'});assert.equal(input.disabled,false);
 input.checked=true;await input.onchange();assert.equal(f.requests.length,2);
 const request=f.requests[1];assert.equal(request.url,'/api/videoAutomatic');assert.equal(request.options.headers['X-Display-CSRF'],'csrf');
 assert.deepEqual(JSON.parse(request.options.body),{revision:2,profileRevision:4,enabled:true});assert.equal(f.controller.rendered,true);
 f.controller.refreshDisplayControl({canControl:false,transactionStatus:'idle'});assert.equal(input.disabled,true);
});
test('navigation during async Settings load cannot append or submit an obsolete control',async()=>{
 const f=fixture();f.controller._automaticRequest=async()=>{f.controller._renderToken++;return {policy:{},profile:{}};};
 await f.controller._renderDisplaySection(1);assert.equal(f.nodes.length,0);
});
