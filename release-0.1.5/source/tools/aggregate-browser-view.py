# SPDX-License-Identifier: GPL-3.0-only
"""Pure public runtime-view compiler. No import, staging or activation authority."""
import hashlib
import io
from pathlib import PurePosixPath
import re
import tarfile

PACKAGE_PIN='01690d5b4efaef3e26a7b30bd58e2c3aaa321ac2f8ff8dd661f9e76d3388ba11'
COMPONENT_PIN='6bf43c26942a9dc2405965907b828df732d3ba167327a419b6196a97443d9761'
ADAPTER_PIN='037856026e0c90b1d80452970c688768eaaadf2908124c3fc9a4fce37e842223'
TARGETS={'portable':'/opt/droiddeck/runtime','legacy':'/opt/teslaandroid-waydroid/runtime'}
ENTRIES={'portable':('portable/worker-entry.js','portable/aggregate-original-worker-entry.js'),
         'legacy':('preview-gateway/server.js','preview-gateway/aggregate-original-server.js')}

def sha(raw):return hashlib.sha256(raw).hexdigest()

def capture_runtime(package):
    if type(package) is not bytes or len(package)!=3348480 or sha(package)!=PACKAGE_PIN:
        raise ValueError('Original qualified public capture package required')
    with tarfile.open(fileobj=io.BytesIO(package),mode='r:') as archive:
        component=archive.extractfile('droiddeck-capture-activation/component.tar').read()
    if sha(component)!=COMPONENT_PIN:raise ValueError('Original complete runtime component required')
    prefix='droiddeck-capture-runtime/runtime/'
    files={}
    with tarfile.open(fileobj=io.BytesIO(component),mode='r:') as archive:
        for item in archive:
            if not item.name.startswith(prefix):continue
            name=item.name[len(prefix):];path=PurePosixPath(name)
            if (item.type!=tarfile.REGTYPE or item.pax_headers or item.sparse is not None or item.linkname
                    or name in files or str(path)!=name or path.is_absolute() or '..' in path.parts
                    or not 0<item.size<=8*1024**2):
                raise ValueError('Closed regular public runtime files required')
            raw=archive.extractfile(item).read()
            if len(raw)!=item.size:raise ValueError('Complete public runtime bytes required')
            files[name]=raw
    if len(files)!=247:raise ValueError('Complete241-file original runtime required')
    return files

def wrapper(mode):
    if mode not in TARGETS:raise ValueError('Explicit browser layout required')
    header='// SPDX-License-Identifier: GPL-3.0-only\n'
    if mode=='portable':
        body="""const original = require('./aggregate-original-worker-entry');
async function main(args) {
  if (args.length !== 1 || args[0] !== 'gateway') throw Error('Original gateway role required');
  return require('./aggregate-gateway').startGateway({ mode: 'portable',
    frontendDir: process.env.DROIDMATE_BROWSER_FRONTEND, args: [], portable: original.startWorker });
}
module.exports = original;
"""
    else:
        body="""const original = require('./aggregate-original-server');
async function main(args) {
  return require('../portable/aggregate-gateway').startGateway({ mode: 'legacy',
    frontendDir: process.env.DROIDMATE_BROWSER_FRONTEND, args, server: original });
}
module.exports = original;
"""
    return (header+body+"if (require.main === module) main(process.argv.slice(2)).catch(() => {\n"+
            "  console.error('Aggregate browser startup rejected'); process.exitCode = 1;\n});\n").encode('ascii')

def runtime_tree(package,adapter,mode):
    if mode not in TARGETS or type(adapter) is not bytes or sha(adapter)!=ADAPTER_PIN:
        raise ValueError('Explicit layout and reviewed browser adapter required')
    files=capture_runtime(package)
    entry,archived=ENTRIES[mode]
    if archived in files or 'portable/aggregate-gateway.js' in files:raise ValueError('Fresh closed aggregate entries required')
    files[archived]=files[entry];files[entry]=wrapper(mode)
    files['portable/aggregate-gateway.js']=adapter
    return dict(sorted(files.items()))

def mapping(mode,release_sha,frontend_sha):
    if mode not in TARGETS or any(type(v) is not str or not re.fullmatch('[a-f0-9]{64}',v) for v in (release_sha,frontend_sha)):
        raise ValueError('Exact layout and content-addressed browser paths required')
    return ('[Service]\nPrivateMounts=yes\nBindReadOnlyPaths=/var/lib/droidmate-browser/'+release_sha+'/'+mode+
            '/runtime:'+TARGETS[mode]+'\nEnvironment=DROIDMATE_BROWSER_FRONTEND=/var/lib/droidmate-browser/'+
            frontend_sha+'/frontend\n').encode('ascii')
