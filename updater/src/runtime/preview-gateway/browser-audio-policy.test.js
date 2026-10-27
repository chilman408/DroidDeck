// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){const rows=[],handlers=new Map(),selections=[],inserted=[];
const node=tag=>({tag,dataset:{},children:[],isConnected:true,append(...n){this.children.push(...n);},appendChild(n){this.children.push(n);},setAttribute(k,v){this[k]=v;}});
const divider={};const browserTile={nextSibling:divider,querySelector:()=>({textContent:'Browser audio'})};
const window={TAHtml:{SettingsController:function(){}},document:{createElement:node},DroidDeckAudio:{snapshot:()=>({requested:'aac',effective:'flac',fallbackReason:'AAC failed; using FLAC.'}),select:value=>selections.push(value)},addEventListener:(n,f)=>handlers.set(n,f),removeEventListener:n=>handlers.delete(n)};
const base=window.TAHtml.SettingsController.prototype;base._renderAudioSection=async()=>{};
base._appendTile=options=>{const row=node('tile');row.options=options;rows.push(row);return row;};
base._createSelect=(options,value,change)=>{const select=node('dropdown');select.options=options;select.value=value;select.change=change;select.trigger=node('button');select.querySelector=()=>select.trigger;select.querySelectorAll=()=>[select.trigger];return select;};
vm.runInNewContext(fs.readFileSync(require.resolve('./browser-audio-policy'),'utf8'),{window});
const c=new window.TAHtml.SettingsController();Object.assign(c,{_renderToken:1,activeSectionId:'audio',content:{querySelectorAll:()=>[browserTile],insertBefore:(n,anchor)=>inserted.push({n,anchor})}});return{window,c,rows,handlers,selections,inserted,divider};}
test('audio format uses the native dropdown directly below Browser audio, stays browser-local and reports fallback',async()=>{const f=fixture();await f.c._renderAudioSection(1);const row=f.rows[0],select=row.options.trailing,status=f.inserted[1].n;
assert.equal(row.options.title,'Audio format');assert.equal(select.value,'aac');assert.equal(select.trigger['aria-label'],'Audio format');assert.equal(select.trigger.dataset.localAudioDelay,'true');assert.equal(f.inserted[0].n,row);assert.equal(f.inserted[0].anchor,f.divider);assert.match(status.textContent,/using FLAC/);
select.change('flac');assert.deepEqual(f.selections,['flac']);f.c._renderToken=2;select.change('aac');assert.deepEqual(f.selections,['flac']);f.handlers.get('droiddeck-audio-format')();assert.equal(f.handlers.size,0);});
test('audio section navigation cannot append an obsolete format control',async()=>{const f=fixture();f.c.activeSectionId='display';await f.c._renderAudioSection(1);assert.equal(f.rows.length,0);});
