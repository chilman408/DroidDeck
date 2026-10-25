// SPDX-License-Identifier: GPL-3.0-only
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
test('Updates permissions follow ownership arriving after the initial status render',()=>{
 const prototype={initialize(){},_renderActiveSection(){},refreshDisplayControl(state){this.displayController.remoteDisplayState=state;}};
 const window={TAHtml:{SettingsController:function(){}},clearTimeout(){}};window.TAHtml.SettingsController.prototype=prototype;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'browser-update-policy.js'),'utf8'),{window});
 const seen=[],controller=Object.create(prototype);
 Object.assign(controller,{activeSectionId:'updates',_updateState:{installedVersion:'0.1.1-rc.7'},_updatesPrimary:false,displayController:{remoteDisplayState:{canControl:false}},
  _paintUpdates(){this._updatesPrimary=this.displayController.remoteDisplayState.canControl;seen.push(this._updatesPrimary);}});
 controller.refreshDisplayControl({canControl:true});controller.refreshDisplayControl({canControl:true});controller.refreshDisplayControl({canControl:false});
 assert.deepEqual(seen,[true,false]);
 controller.activeSectionId='display';controller.refreshDisplayControl({canControl:true});assert.deepEqual(seen,[true,false]);
});
test('a successful check with no offer gives explicit feedback instead of repeating the initial prompt',async()=>{
 const prototype={initialize(){},_renderActiveSection(){},refreshDisplayControl(){}};
 const node=tag=>({tag,textContent:'',style:{},setAttribute(name,value){this[name]=value;}});
 const window={TAHtml:{SettingsController:function(){}},document:{createElement:node},clearTimeout(){},setTimeout(){return 1;}};
 window.TAHtml.SettingsController.prototype=prototype;
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'browser-update-policy.js'),'utf8'),{window});
 const children=[],state={installedVersion:'0.1.1-rc.11',available:null,job:null,rollback:null};
 const controller=Object.create(prototype);Object.assign(controller,{activeSectionId:'updates',displayController:{remoteDisplayState:{canControl:true}},
  content:{dataset:{},replaceChildren(...nodes){children.splice(0,children.length,...nodes);},appendChild(n){children.push(n);}},async _updateRequest(){return state;}});
 controller._paintUpdates(state);
 await children.find(n=>n.tag==='button'&&n.textContent==='Check for updates').onclick();
 assert.equal(children.find(n=>n.role==='status').textContent,'No newer pilot release is available.');
});
