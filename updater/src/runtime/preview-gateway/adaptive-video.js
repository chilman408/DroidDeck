// SPDX-License-Identifier: GPL-3.0-only
// Relay-measured, owner-scoped feedback. No display resize or browser-supplied rate authority.
const { BIT_RATES, automaticFrameLimit } = require('./video-settings');
function createAdaptiveVideo({ store, clock = () => performance.now() }) {
  let previous, identity, baseline, badSince, healthySince, packets, bytes, changedAt, probe, promoteAfter;
  function evidence() { previous=null;badSince=healthySince=null;packets=bytes=0; }
  function reset() { evidence();identity=null;baseline=Infinity;changedAt=-Infinity;probe=null;promoteAfter=-Infinity; }
  reset();
  return { reset, observe(sample, authority) {
    const now=clock(),policy=store.automaticSnapshot(),profile=store.snapshot();
    if(!Number.isFinite(now)||!policy.enabled||!sample?.available||sample.flow!==true||typeof authority!=='string'||!authority||authority.length>256||!/^[a-f0-9]{16}$/.test(sample.streamId||'')) {reset();return null;}
    const fields=['ackedPackets','acknowledgedBytes','pendingBytes','pendingFrames','oldestMs','ackDelayMs'];
    if(fields.some(key=>!Number.isSafeInteger(sample[key])||sample[key]<0)){reset();return null;}
    const key=authority+'|'+sample.streamId+'|'+policy.revision;
    if(identity!==key){reset();identity=key;}
    if(!previous||previous.revision!==profile.revision||now<previous.at||now-previous.at>4000||sample.ackedPackets<previous.ackedPackets||sample.acknowledgedBytes<previous.acknowledgedBytes){
      evidence();previous={...sample,revision:profile.revision,at:now};return null;
    }
    const elapsed=now-previous.at,received=sample.ackedPackets-previous.ackedPackets,delivered=sample.acknowledgedBytes-previous.acknowledgedBytes;
    if(elapsed<750)return null;
    previous={...sample,revision:profile.revision,at:now};
    if(received>0&&sample.pendingFrames<=2)baseline=Math.min(baseline,sample.ackDelayMs);
    const floor=Number.isFinite(baseline)?baseline:0,extraAck=Math.max(0,sample.ackDelayMs-floor),extraOld=Math.max(0,sample.oldestMs-floor);
    const pending=sample.pendingFrames>0||sample.pendingBytes>0;
    const bad=pending&&(extraOld>=350||received>0&&extraAck>=250&&sample.pendingFrames>=3);
    const severe=pending&&(extraOld>=900||sample.oldestMs>=2500||received>0&&extraAck>=800);
    const good=received>0&&delivered>0&&extraOld<250&&extraAck<150&&sample.pendingFrames<=2;
    if(bad){if(badSince===null)badSince=now;}else badSince=null;
    if(good){if(healthySince===null)healthySince=now;packets=Math.min(16,packets+received);bytes=Math.min(32768,bytes+delivered);}
    else{healthySince=null;packets=bytes=0;}
    let bitRate=profile.bitRate,maxFps=profile.maxFps;
    if(bad&&probe&&now-probe.at<=4000&&now-changedAt>=1000){
      bitRate=Math.min(probe.bitRate,policy.ceilingBitRate);maxFps=Math.min(probe.maxFps,policy.ceilingMaxFps,automaticFrameLimit(bitRate));
      promoteAfter=now+10000;probe=null;
    }else if((severe||badSince!==null&&now-badSince>=1000)&&now-changedAt>=2000){
      const lower=BIT_RATES.filter(rate=>rate<bitRate);
      if(lower.length){
        // A loaded delivery estimate informs cuts only. Quiet scenes never cap recovery.
        const measured=received>=8&&delivered>=32768?Math.max(320000,delivered*8000/elapsed*.75-160000):Infinity;
        const target=Math.min(bitRate*(severe ? 0.5 : 0.75),measured);
        bitRate=lower.filter(rate=>rate<=target).at(-1)||lower[0];
        maxFps=Math.min(maxFps,30,automaticFrameLimit(bitRate));
      }else maxFps=Math.min(maxFps,automaticFrameLimit(bitRate));
      promoteAfter=Math.max(promoteAfter,now+3000);probe=null;
    }else if(good&&healthySince!==null&&now-healthySince>=3000&&packets>=4&&bytes>=1024&&now-changedAt>=3000&&now>=promoteAfter){
      const higher=BIT_RATES.find(rate=>rate>bitRate&&rate<=policy.ceilingBitRate);
      if(higher){bitRate=higher;maxFps=Math.min(policy.ceilingMaxFps,Math.max(maxFps,Math.min(30,automaticFrameLimit(bitRate))));}
      else if(maxFps<policy.ceilingMaxFps)maxFps=Math.min(policy.ceilingMaxFps,automaticFrameLimit(bitRate),maxFps<30?30:maxFps<45?45:60);
      if(bitRate!==profile.bitRate||maxFps!==profile.maxFps)probe={at:now,bitRate:profile.bitRate,maxFps:profile.maxFps};
    }
    maxFps=Math.min(maxFps,policy.ceilingMaxFps,automaticFrameLimit(bitRate));
    if(bitRate===profile.bitRate&&maxFps===profile.maxFps)return null;
    const next=store.adapt({revision:profile.revision,bitRate,maxFps},policy.revision);
    changedAt=now;evidence();previous={...sample,revision:next.revision,at:now};return next;
  }};
}
module.exports={createAdaptiveVideo};
