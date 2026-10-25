// SPDX-License-Identifier: GPL-3.0-only
// Standalone generated-pattern self-test. No Android connections or telemetry.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function diagnosticWorker() {
  function pattern(pixels) {
    const colours = new Set(pixels.map(([r, g, b]) => r > 150 && g > 150 && b > 150 ? 'white' :
      r > 150 && g < 100 && b < 100 ? 'red' : g > 150 && r < 100 && b < 100 ? 'green' :
      b > 150 && r < 100 && g < 100 ? 'blue' : 'unknown'));
    return ['red', 'green', 'blue', 'white'].every(c => colours.has(c));
  }
  function draw(frame, kind, width, height) {
    const canvas = new OffscreenCanvas(width, height);
    const samples = [[40, 40], [240, 40], [40, 140], [240, 140]];
    if (kind === '2d') {
      const context = canvas.getContext('2d', { alpha: false });
      if (!context) return { kind, available: false };
      context.drawImage(frame, 0, 0, width, height);
      const pixels = samples.map(([x, y]) => Array.from(context.getImageData(x, y, 1, 1).data).slice(0, 3));
      return { kind, available: true, patternVisible: pattern(pixels) };
    }
    const gl = canvas.getContext(kind, { preserveDrawingBuffer: true });
    if (!gl) return { kind, available: false };
    const shaders = [[gl.VERTEX_SHADER,
      'attribute vec2 p;attribute vec2 t;varying vec2 uv;void main(){gl_Position=vec4(p,0.,1.);uv=t;}'],
    [gl.FRAGMENT_SHADER,
      'precision mediump float;uniform sampler2D image;varying vec2 uv;void main(){gl_FragColor=texture2D(image,uv);}']];
    const program = gl.createProgram();
    for (const [type, source] of shaders) {
      const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return { kind, available: true, shaderCompiled: false };
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return { kind, available: true, programLinked: false };
    gl.useProgram(program);
    for (const [name, data] of [['p', [-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]], ['t', [0,1,1,1,0,0,0,0,1,1,1,0]]]) {
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
      const location = gl.getAttribLocation(program, name); gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
    }
    gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, frame);
    gl.viewport(0, 0, width, height); gl.drawArrays(gl.TRIANGLES, 0, 6);
    const pixels = samples.map(([x, y]) => { const pixel = new Uint8Array(4); gl.readPixels(x,y,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel); return Array.from(pixel).slice(0,3); });
    const result = { kind, available: true, shaderCompiled: true, programLinked: true,
      glError: gl.getError(), patternVisible: pattern(pixels) };
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return result;
  }
  self.onmessage = async ({ data: fixture }) => {
    self.postMessage({ stage: 'worker', workerDecoderAvailable: typeof VideoDecoder === 'function',
      workerCanvasAvailable: typeof OffscreenCanvas === 'function' });
    if (typeof VideoDecoder !== 'function' || typeof OffscreenCanvas !== 'function') {
      self.postMessage({ done: true }); return;
    }
    const bytes = Uint8Array.from(atob(fixture.data), c => c.charCodeAt(0));
    for (const preference of ['no-preference', 'prefer-hardware', 'prefer-software']) {
      const config = { codec: fixture.codec, codedWidth: fixture.width, codedHeight: fixture.height,
        hardwareAcceleration: preference, optimizeForLatency: true };
      let decoder, frame;
      try {
        const support = await Promise.race([VideoDecoder.isConfigSupported(config),
          new Promise((_, reject) => setTimeout(() => reject(new Error('CapabilityTimeout')), 5000))]);
        if (!support.supported) { self.postMessage({ preference, supported: false }); continue; }
        frame = await new Promise((resolve, reject) => {
          let done = false;
          const finish = (error, result) => { if (done) { result?.close(); return; } done = true; clearTimeout(timer); error ? reject(error) : resolve(result); };
          const timer = setTimeout(() => finish(new Error('DecodeTimeout')), 5000);
          try {
            decoder = new VideoDecoder({ output: frame => finish(null, frame), error: error => finish(error) });
            decoder.configure(config);
            decoder.decode(new EncodedVideoChunk({ type: 'key', timestamp: 0, data: bytes }));
            decoder.flush().catch(error => finish(error));
          } catch (error) { finish(error); }
        });
        const presentation = [];
        for (const kind of ['2d', 'webgl', 'webgl2']) {
          try { presentation.push(draw(frame, kind, fixture.width, fixture.height)); }
          catch (error) { presentation.push({ kind, error: error.name }); }
        }
        self.postMessage({ preference, supported: true, decoded: true, presentation });
      } catch (error) { self.postMessage({ preference, error: error.name, stage: error.message === 'DecodeTimeout' ? 'decode-timeout' : error.message === 'CapabilityTimeout' ? 'capability-timeout' : 'decoder' }); }
      finally { frame?.close(); if (decoder && decoder.state !== 'closed') decoder.close(); }
    }
    self.postMessage({ done: true });
  };
}

function build() {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/webcodecs-colour-pattern.json'), 'utf8'));
  if (fixture.kind !== 'generated-four-colour-h264-pattern' || fixture.width !== 320 || fixture.height !== 180 ||
      !/^avc1\.[a-f0-9]{6}$/.test(fixture.codec) || Buffer.from(fixture.data, 'base64').length > 16384) throw new Error('Fixed generated fixture required');
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DroidDeck WebCodecs self-test</title><style>body{font:18px system-ui;margin:24px;background:#172126;color:#eef7f1}button{font:inherit;padding:12px 24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0e1519;padding:16px}p{max-width:70ch}</style>
<h1>WebCodecs self-test</h1><p>Run while parked. This uses a generated colour pattern only. It does not connect to Android, change settings, or upload results.</p>
<p>The test compares native decoding with 2D, WebGL and WebGL2 drawing. It does not measure CPU usage or prove hardware acceleration.</p>
<button id="run">Run test</button><pre id="result">Ready.</pre><script>
const fixture=${JSON.stringify(fixture)}, source=${JSON.stringify('(' + diagnosticWorker.toString() + ')()')};
document.getElementById('run').onclick=()=>{
const report={kind:'generated-webcodecs-self-test',secure:isSecureContext,mainDecoderAvailable:typeof VideoDecoder==='function',codec:fixture.codec,results:[]};
const output=document.getElementById('result'),button=document.getElementById('run');button.disabled=true;let worker,timer,blob;
function show(){output.textContent=JSON.stringify(report,null,2)}
function finish(){clearTimeout(timer);worker?.terminate();if(blob)URL.revokeObjectURL(blob);button.disabled=false;show()}
try{blob=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));worker=new Worker(blob);
worker.onmessage=event=>{const value=event.data;report.results.push(value);show();if(value.done)finish()};
worker.onerror=event=>{report.results.push({stage:'worker-error'});finish()};
timer=setTimeout(()=>{report.results.push({stage:'overall-timeout'});finish()},40000);worker.postMessage(fixture);show();
}catch(error){report.results.push({stage:'worker-construction',error:error.name});finish()}
};</script></html>`;
}
if (require.main === module) {
  if (process.argv.length !== 3) throw new Error('NEW_HTML_FILE required');
  const raw = Buffer.from(build()); fs.writeFileSync(process.argv[2], raw, { flag: 'wx', mode: 0o600 });
  console.log(JSON.stringify({ generatedOnly: true, bytes: raw.length, sha256: crypto.createHash('sha256').update(raw).digest('hex') }));
}
module.exports = { build };
