// SPDX-License-Identifier: GPL-3.0-only
// Opt-in diagnostic loader only. Original frontend and decoder remain unchanged.
const fs = require('node:fs');
const crypto = require('node:crypto');
const KEY = 'DroidDeck_liveCodecDiagnostic_20261024ic';

function observe(key, workerPolicy) {
  const report = {kind:'live-webcodecs-diagnostic',secure:isSecureContext,
    workerCreated:false,configured:false,packets:0,configPackets:0,keyPackets:0,
    rendered:0,canvasFrames:0};
  if(workerPolicy) {report.compatibility='omit-aud';report.audRemoved=0;}
  const blobUrls=[];
  const start = performance.now();
  const number = value => Number.isFinite(Number(value)) ? Math.max(0,Math.min(1000000,Math.round(Number(value)))) : 0;
  const category = value => {
    const text=String(value || '');
    if(text==='WebCodecs configuration unavailable; use legacy H264') return 'ConfigurationUnavailable';
    if(text==='H264 decode buffer exceeded') return 'DecodeBufferExceeded';
    return /^(NotSupportedError|InvalidStateError|DataError|EncodingError|OperationError|TypeError|ReferenceError|SecurityError|NetworkError)\b/.exec(text)?.[1] || 'WorkerReportedError';
  };
  function sample() {
    report.elapsedMs = number(performance.now()-start);
    report.canvasFrames = number(document.getElementById('canvas')?.dataset.videoFrames);
    report.effective = window.TAHtml?.browserRenderer?.effectiveChoice() === 'webcodecs' ? 'webcodecs' : 'other';
  }
  function save() {
    sample();
    try { sessionStorage.setItem(key,JSON.stringify(report)); }
    catch {
      report.storageFailed=true;
      // A fallback destination may already be fixed during beforeunload.
      // Remove stale evidence independently of the departing page's URL.
      try {sessionStorage.removeItem(key);} catch {}
      const url=new URL(location.href);url.searchParams.set('codec-report-storage','failed');
      history.replaceState(null,'',url.href);
      const button=document.getElementById('codec-finish');
      if(button) button.textContent='Diagnostic report storage failed';
    }
  }
  const NativeWorker = window.Worker;
  window.Worker = class extends NativeWorker {
    constructor(url, options) {
      const target = new URL(url,document.baseURI);
      let actual=url,blob;
      if(workerPolicy && target.origin===location.origin && target.pathname==='/js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js') {
        blob=URL.createObjectURL(new Blob(['importScripts('+JSON.stringify(target.href)+');\n'+workerPolicy],{type:'text/javascript'}));
        actual=blob;
      }
      try {super(actual,options);} catch(error) {if(blob)URL.revokeObjectURL(blob);throw error;}
      if(blob) {blobUrls.push(blob);this.__blobUrl=blob;}
      if (target.origin !== location.origin || target.pathname !== '/js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js') return;
      report.workerCreated = true;
      this.addEventListener('message', event => {
        const data = event.data;
        if (!data || typeof data !== 'object') return;
        if (data.error) report.errorCategory = category(data.error);
        if(data.error && /key frame is required|wasn't a key frame/i.test(String(data.error))) report.errorDetail='KeyFrameRequired';
        if(workerPolicy && Number.isInteger(data.webCodecsAudRemoved) && data.webCodecsAudRemoved>0) report.audRemoved=number(report.audRemoved+data.webCodecsAudRemoved);
        if (data.webCodecsConfigured === true) report.configured = true;
        if (data.frameRendered === true) report.rendered = number(report.rendered+1);
        if(data.error || data.webCodecsConfigured === true || report.rendered === 1 || (workerPolicy && report.audRemoved===1)) save();
      });
      this.addEventListener('error', event => { report.workerError = category(event.message?.replace(/^Uncaught\s+/,'')); save(); });
      const post = this.postMessage.bind(this);
      this.postMessage = function (data,...rest) {
        if (data?.scrcpySession) report.session = {width:number(data.scrcpySession.width),height:number(data.scrcpySession.height)};
        const packet = data?.scrcpyPacket;
        if (packet instanceof ArrayBuffer && packet.byteLength >= 13) {
          report.packets = number(report.packets+1);
          const bytes = new Uint8Array(packet);
          if (bytes[0]&0x20) report.keyPackets = number(report.keyPackets+1);
          if (bytes[0]&0x40) {
            report.configPackets = number(report.configPackets+1);
            // Codec configuration bytes only; never retain a video payload.
            for (let i=12;i+7<Math.min(bytes.length,512);i++) {
              let offset=0;
              if(bytes[i]===0 && bytes[i+1]===0 && bytes[i+2]===1) offset=3;
              else if(bytes[i]===0 && bytes[i+1]===0 && bytes[i+2]===0 && bytes[i+3]===1) offset=4;
              if(offset && (bytes[i+offset]&31)===7) {
                report.codec='avc1.'+Array.from(bytes.subarray(i+offset+1,i+offset+4),v=>v.toString(16).padStart(2,'0')).join(''); break;
              }
            }
          }
        }
        return post(data,...rest);
      };
      save();
    }
  };
  window.addEventListener('beforeunload',()=>{save();for(const blob of blobUrls)URL.revokeObjectURL(blob);});
  function finish() {
    save(); const url=new URL(location.href);url.searchParams.set('video','report');location.replace(url.href);
  }
  window.addEventListener('DOMContentLoaded',()=>{
    const button=document.createElement('button');button.id='codec-finish';button.textContent='Finish diagnostic and show report';
    button.style.cssText='position:fixed;top:8px;right:8px;z-index:2147483647;padding:12px;font:18px system-ui';
    button.onclick=finish;document.body.appendChild(button);
    if(report.storageFailed) button.textContent='Diagnostic report storage failed';
  });
  // This finite bound exists only in this opt-in diagnostic, never the preview.
  setTimeout(finish,45000);
  save();
}

function launch(key, observerSource, workerPolicy) {
  const output=document.getElementById('codec-result');
  const query=new URLSearchParams(location.search).get('video');
  if(query !== 'webcodecs-test') {
    try {
      const raw=sessionStorage.getItem(key);
      const value=raw && raw.length<8192 ? JSON.parse(raw) : null;
      if(new URLSearchParams(location.search).get('codec-report-storage')==='failed') output.textContent='Report storage failed during this diagnostic; retained evidence is unavailable.';
      else if(value?.kind==='live-webcodecs-diagnostic') {value.finalVideoQuery=query;output.textContent=JSON.stringify(value,null,2);}
      else if(query) output.textContent='No diagnostic report was retained. Retry with session storage available.';
    } catch { output.textContent='Session report unavailable. Allow session storage for this diagnostic.'; }
    document.getElementById('codec-run').onclick=()=>{const url=new URL(location.href);url.searchParams.set('video','webcodecs-test');url.searchParams.delete('codec-report-storage');location.assign(url.href);};
    return;
  }
  try {
    sessionStorage.removeItem(key);
    const probe=JSON.stringify({kind:'live-webcodecs-diagnostic',stage:'starting'});
    sessionStorage.setItem(key,probe);
    if(sessionStorage.getItem(key)!==probe) throw new Error('StorageUnavailable');
  } catch {output.textContent='Session storage is required to retain fallback evidence.';return;}
  const controller=new AbortController();const startupTimer=setTimeout(()=>controller.abort(),10000);
  fetch('/',{cache:'no-store',credentials:'same-origin',signal:controller.signal}).then(async response=>{
    if(!response.ok || response.redirected) throw new Error('FrontendUnavailable');
    const html=await response.text();
    if(html.length>131072 || !/<title>DroidDeck<\/title>/i.test(html) || !/<head\b[^>]*>/i.test(html)) throw new Error('FrontendUnavailable');
    clearTimeout(startupTimer);
    const boot='<base href="/">'+'<script>('+observerSource+')('+JSON.stringify(key)+','+JSON.stringify(workerPolicy)+');<\/script>';
    document.open();document.write(html.replace(/<head\b[^>]*>/i,match=>match+boot));document.close();
  }).catch(()=>{clearTimeout(startupTimer);output.textContent='Unable to load the existing same-origin DroidDeck frontend.';});
}

function build({audCompatibility=false}={}) {
  if(typeof audCompatibility!=='boolean') throw new Error('Fixed boolean diagnostic variant required');
  const workerPolicy=audCompatibility?require('./webcodecs-access-unit-policy').workerPolicySource():'';
  const key=KEY+(audCompatibility?'_aud_20261024id':'');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DroidDeck live codec diagnostic</title></head>
<body style="font:18px system-ui;background:#172126;color:#eef7f1;margin:24px"><h1>Live WebCodecs diagnostic</h1>
<p>Run while parked. This briefly opens the existing Android stream and records decoder metadata only. No video or pictures are saved or uploaded. The diagnostic ends after 45 seconds or on legacy fallback.${audCompatibility?' This compatibility trial removes AUD separators only; it preserves all picture slices.':''}</p>
<button id="codec-run" style="font:inherit;padding:12px">Run live diagnostic</button>
<p><a href="/" style="color:#9effbb">Return to normal DroidDeck</a></p><pre id="codec-result" style="white-space:pre-wrap;overflow-wrap:anywhere">Ready. Results remain in this browser tab.</pre>
<script>(${launch.toString()})(${JSON.stringify(key)},${JSON.stringify(observe.toString())},${JSON.stringify(workerPolicy)});</script></body></html>`;
}
if(require.main===module) {
  if(process.argv.length!==3) throw new Error('NEW_HTML_FILE required');
  const raw=Buffer.from(build());fs.writeFileSync(process.argv[2],raw,{flag:'wx',mode:0o600});
  console.log(JSON.stringify({bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex'),metadataOnly:true}));
}
module.exports={build};
