# SPDX-License-Identifier: GPL-3.0-only
"""Build a code-only legacy preview backport. Does not install or read VM data."""
from pathlib import Path
import hashlib,json,sys
PINS={'server.js':'7f3b3dfa88842fde4c52b7734da865f262da91b46da717a9d3fd0e8fd487d266','audio-relay.js':'b42b31949db91e3dd6e7fcc0ddbdba553c3ddafbc459401d4da647b18a780178','h264-relay.js':'c6ee510353c297106641572bb1e61b0aa85fceb93b5524212cfd0b40975bd272','video-settings.js':'73cb85b2b7a103f549b74a95fb4e7ef6152bb8c917f3603189e803b741604767'}
EXTRAS=('adaptive-video.js','browser-adaptive-policy.js','browser-audio-policy.js','browser-audio-playback.js','browser-audio-worker.js')
def segment(s,start,end):
 if s.count(start)!=1 or s.count(end)!=1:raise ValueError('Unambiguous source anchors required')
 return s[s.index(start):s.index(end)]
def replace(s,old,new):
 if s.count(old)!=1:raise ValueError('Exact legacy anchor required: '+old[:50])
 return s.replace(old,new)
def build(original,source):
 old={n:(original/n).read_bytes() for n in PINS}
 if any(hashlib.sha256(old[n]).hexdigest()!=h for n,h in PINS.items()):raise ValueError('Exact audited legacy code required')
 read=lambda n:(source/n).read_text(encoding='utf-8')
 current=read('server.js');server=old['server.js'].decode()
 adaptive=segment(current,'  const videoSessions = new Map();','  const sessionExpired = token => {')
 server=replace(server,'  const sessionExpired = token => {',adaptive+'  const sessionExpired = token => {')
 route=segment(current,'    let decodedUpdatePath;','    if (decodedUpdatePath === \'/api/updates\') {')
 server=replace(server,'    if ((backends.audio || gpsControl) && pathname === "/api/configuration") {',route+'    if ((backends.audio || gpsControl) && pathname === "/api/configuration") {')
 assets=segment(current,'    const audioAssets = {','    const browserPolicies = {')
 assets+='''    if (decoded === '/js/settings/settings-controller.js' && ['GET','HEAD'].includes(req.method)) {
      const body = Buffer.concat([fs.readFileSync(path.join(frontendDir, decoded.slice(1))), Buffer.from('\\n;\\n'),
        fs.readFileSync(path.join(__dirname, 'browser-adaptive-policy.js')), Buffer.from('\\n;\\n'),
        fs.readFileSync(path.join(__dirname, 'browser-audio-policy.js'))]);
      res.setHeader('Content-Type', 'text/javascript; charset=utf-8'); res.setHeader('Content-Length', body.length);
      res.end(req.method === 'HEAD' ? undefined : body); return;
    }
'''
 server=replace(server,'    serve(req, res, { public: frontendDir, directoryListing: false, cleanUrls: false })',assets+'    serve(req, res, { public: frontendDir, directoryListing: false, cleanUrls: false })')
 server=replace(server,'    let target = pathname === "/sockets/display" ?',"    delete req.headers['x-droiddeck-display-session'];\n    let target = pathname === \"/sockets/display\" ?")
 flow=segment(current,'      if (formats.length) {\n        req.headers[','      req.url = pathname;')
 server=replace(server,'      req.url = pathname;',flow+'      req.url = pathname;')
 server=replace(server,'      closing = true; closeStreams();','      closing = true; clearInterval(adaptiveTimer); adaptiveVideo?.reset(); closeStreams();')
 server=replace(server,'  return server;\n}',"  if (adaptiveVideo) server.once('listening', () => { adaptiveTimer = setInterval(() => { void adaptiveTick(); }, 1000); adaptiveTimer.unref(); });\n  return server;\n}")
 result={n:(source/n).read_bytes() for n in EXTRAS};result['server.js']=server.encode();result['video-settings.js']=(source/'video-settings.js').read_bytes()
 for name,func in [('audio-relay.js','async function main('),('h264-relay.js','async function main(')]:
  body=read(name).split(func)[0]
  body=body.replace("const { loadWorkerConfiguration } = require('../portable/worker-config');\n",'').replace("const { captureLayout } = require('./capture-layout');\n",'')
  originalMain=old[name].decode();body+=originalMain[originalMain.index('if (require.main === module) {'):]
  result[name]=body.encode()
 return result
if __name__=='__main__':
 if len(sys.argv)!=4:raise ValueError('ORIGINAL_CODE_DIRECTORY CURRENT_GATEWAY_SOURCE NEW_OUTPUT_DIRECTORY')
 result=build(Path(sys.argv[1]),Path(sys.argv[2]));out=Path(sys.argv[3]);out.mkdir(mode=0o700)
 for n,b in result.items():(out/n).write_bytes(b)
 (out/'manifest.json').write_text(json.dumps({'kind':'legacy-preview-code-backport','installer':False,'originalSha256':PINS,'afterSha256':{n:hashlib.sha256(b).hexdigest() for n,b in sorted(result.items())}},sort_keys=True)+'\n',encoding='utf-8')
 print('Legacy code-only backport built',len(result),'files; no installation or Android changes')
