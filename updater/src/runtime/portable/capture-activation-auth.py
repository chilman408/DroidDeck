# SPDX-License-Identifier: GPL-3.0-only
"""Separate capture activation envelope. Verify only; never extract or switch units."""
import ast
import hashlib
import importlib.util
import io
from pathlib import Path
import re
import sys
import tarfile

spec=importlib.util.spec_from_file_location('activation_component',Path(__file__).with_name('capture-component-auth.py'))
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C);P=C.P
PREFIX='droiddeck-capture-activation/'
MANIFEST=PREFIX+'manifest.json'
COMPONENT=PREFIX+'component.tar'
KIND='droiddeck-capture-activation-package'
ATTESTATION='droiddeck-capture-activation-authorization'
CONTROL_NAMES=tuple(sorted((
    'capture-activation-auth.py','capture-component-auth.py','capture-component-stage.py',
    'capture-private-view.py','capture-release-authority.py','capture-release-verifier.json',
    'capture-runtime-package.py','capture-policy-controller.py','capture-policy-transaction.py',
    'capture-video-admission.py','capture-video-recycle.py','capture-view-controller.py','gpu-native.js','recovery-probe-io.js','effective-units.py',
    'runtime-candidate-admission.py','runtime-candidate-dependencies.py','update-admission.py',
    'update-package.py','update-target.py','preview-gateway/release-version.json',
)))
CONTRACT={'schema':1,'scope':'capture-worker-only','modes':['cpu','gpu'],
    'originalUpdaterLease':'required','componentAndViewOwnership':'required',
    'exactEffectiveGraph':'required','heldNativeSource':'required',
    'durablePreviousWorkerRestore':'required','crossBootAdmission':'independent',
    'originalRuntimeAndConfig':'preserve-sealed-namespace','androidData':'preserve',
    'unraidChanges':False,'healthyExpiry':False}
MAX_CONTROL=1048576
MAX_ARCHIVE=C.C.MAX_ARCHIVE+4*1048576
STATEMENT_KEYS={'schema','kind','channel','purpose','activationAllowed','keySha256',
    'packageSha256','manifestSha256','componentSha256','controlSourcesSha256',
    'sourceRevision','compatibleReleaseSha256','contractSha256'}


def source_path(name):
    if name not in CONTROL_NAMES:raise ValueError('Fixed control-source path required')
    return 'runtime/'+name if name=='preview-gateway/release-version.json' else 'runtime/portable/'+name


def records(files):
    return [{'path':n,'size':len(raw),'sha256':P.sha(raw)} for n,raw in sorted(files.items())]


def controls(files):
    if set(files)!=set(CONTROL_NAMES):raise ValueError('Complete fixed control-source set required')
    for name,raw in files.items():
        if type(raw) is not bytes or not 0<len(raw)<=MAX_CONTROL:raise ValueError('Bounded public control code required')
        if name.endswith('.py'):ast.parse(raw,filename=name)
    if P.sha(files['capture-release-verifier.json'])!=C.C.CATALOG_PIN:
        raise ValueError('Original installed-release verifier catalog required')
    if files['preview-gateway/release-version.json']!=P.encode({'version':'0.1.1-rc.20'}):
        raise ValueError('Exact compatible runtime version asset required')
    return P.sha(P.encode(records(files)))


def revision(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{40}',value):raise ValueError('Exact Git source revision required')
    return value


def selected_controls(repo,commit):
    revision(commit);git=C.C.T.git
    raw=git(repo,['cat-file','commit',commit])
    if hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=commit:raise ValueError('Original commit required')
    names={source_path(n):n for n in CONTROL_NAMES}
    result={}
    for row in git(repo,['ls-tree','-rz','--long',commit,'--',*names]).split(b'\0'):
        if not row:continue
        match=re.fullmatch(rb'(100644|100755) blob ([a-f0-9]{40}) +([0-9]+)\t(.+)',row)
        if not match:raise ValueError('Regular Git control blobs required')
        _mode,obj,size,name=match.groups();name=name.decode('ascii')
        if name not in names or names[name] in result or not 0<int(size)<=MAX_CONTROL:raise ValueError('Closed Git control inputs required')
        data=git(repo,['cat-file','blob',obj.decode('ascii')])
        if len(data)!=int(size) or hashlib.sha1(b'blob '+size+b'\0'+data).hexdigest().encode()!=obj:
            raise ValueError('Original control blob required')
        result[names[name]]=data
    controls(result)
    return result


def make_archive(component,commit,files):
    revision(commit);nested=C.C.review(component,P.sha(component));digest=controls(files)
    manifest={'schema':1,'kind':KIND,'sourceRevision':commit,'compatibleReleaseSha256':C.BASE_RELEASE,
        'componentSha256':nested['bundleSha256'],'componentManifestSha256':nested['manifestSha256'],
        'controlSourcesSha256':digest,'contract':CONTRACT,'controls':records(files)}
    entries={COMPONENT:(component,0o644),MANIFEST:(P.encode(manifest),0o644),
        **{PREFIX+'control/'+n:(raw,0o644) for n,raw in files.items()}}
    raw=P.canonical_archive(entries);review(raw,P.sha(raw));return raw


def unpack(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_ARCHIVE:raise ValueError('Bounded activation envelope required')
    allowed={MANIFEST,COMPONENT,*(PREFIX+'control/'+n for n in CONTROL_NAMES)}
    members={};total=0
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for item in archive:
            limit=C.C.MAX_ARCHIVE if item.name==COMPONENT else MAX_CONTROL
            if (item.name not in allowed or item.name in members or item.type!=tarfile.REGTYPE
                    or item.pax_headers or item.sparse is not None or item.mode!=0o644
                    or item.uid!=0 or item.gid!=0 or item.uname or item.gname or item.mtime!=0
                    or type(item.size) is not int or not 0<item.size<=limit):raise ValueError('Closed canonical activation archive required')
            total+=item.size
            if total>MAX_ARCHIVE:raise ValueError('Bounded activation payload required')
            data=archive.extractfile(item).read(limit+1)
            if len(data)!=item.size:raise ValueError('Complete activation member required')
            members[item.name]=(data,item.mode)
    if set(members)!=allowed or P.canonical_archive(members)!=raw:raise ValueError('Exact complete canonical activation bytes required')
    return members


def review(raw,expected):
    if P.sha(raw)!=P.digest(expected):raise ValueError('Activation package digest differs')
    members=unpack(raw);manifest=P.document(members[MANIFEST][0])
    if (type(manifest) is not dict or set(manifest)!={'schema','kind','sourceRevision','compatibleReleaseSha256',
            'componentSha256','componentManifestSha256','controlSourcesSha256','contract','controls'}
            or type(manifest['schema']) is not int or manifest['schema']!=1 or manifest['kind']!=KIND
            or manifest['compatibleReleaseSha256']!=C.BASE_RELEASE or P.encode(manifest['contract'])!=P.encode(CONTRACT)):
        raise ValueError('Separate exact activation contract required')
    revision(manifest['sourceRevision'])
    component=members[COMPONENT][0];nested=C.C.review(component,P.sha(component))
    files={n:members[PREFIX+'control/'+n][0] for n in CONTROL_NAMES}
    digest=controls(files)
    if (manifest['componentSha256']!=nested['bundleSha256'] or manifest['componentManifestSha256']!=nested['manifestSha256']
            or manifest['controlSourcesSha256']!=digest or P.encode(manifest['controls'])!=P.encode(records(files))):
        raise ValueError('Exact runtime and complete control-source binding required')
    return {'kind':KIND,'packageSha256':expected,'manifestSha256':P.sha(members[MANIFEST][0]),
        'componentSha256':nested['bundleSha256'],'controlSourcesSha256':digest,'controlFiles':len(files),
        'sourceRevision':manifest['sourceRevision'],'compatibleReleaseSha256':C.BASE_RELEASE,
        'payloadExecuted':False,'servicesChanged':False,'readyForActivation':False}


def statement(package,key):
    result=review(package,P.sha(package))
    return P.encode({'schema':1,'kind':ATTESTATION,'channel':'pilot','purpose':'guarded-capture-activation',
        'activationAllowed':True,'keySha256':P.digest(key),
        **{k:result[k] for k in ('packageSha256','manifestSha256','componentSha256','controlSourcesSha256','sourceRevision','compatibleReleaseSha256')},
        'contractSha256':P.sha(P.encode(CONTRACT))})


def review_signing_inputs(package,signed_statement,key):
    if type(signed_statement) is not bytes or not 0<len(signed_statement)<=8192:raise ValueError('Bounded activation statement required')
    value=P.document(signed_statement)
    if (type(value) is not dict or set(value)!=STATEMENT_KEYS or type(value['schema']) is not int
            or value['schema']!=1 or value['kind']!=ATTESTATION or value['channel']!='pilot'
            or value['purpose']!='guarded-capture-activation' or value['activationAllowed'] is not True
            or statement(package,key)!=signed_statement):raise ValueError('Exact separate activation authorization required')
    return {'kind':'droiddeck-capture-activation-signing-inputs','keySha256':key,
        'statementSha256':P.sha(signed_statement),'packageSha256':P.sha(package),
        'activationStatement':True,'payloadExecuted':False,'servicesChanged':False}


def authenticate(package,signed_statement,signature,trust_raw):
    key=C.A.trust(trust_raw)
    report=review_signing_inputs(package,signed_statement,P.sha(key))
    C.A.signature(key,signed_statement,signature)
    return {**report,'kind':'authenticated-capture-activation-authorization','signatureSha256':P.sha(signature),
        'activationStatementAuthenticated':True,'liveInstallationOwnershipVerified':False,
        'effectiveGraphVerified':False,'nativeLeaseVerified':False,'readyForActivation':False}


def main(args):
    if len(args)==5 and args[0]=='build':
        data=make_archive(C.C.read_input(args[1],C.C.MAX_ARCHIVE),args[3],selected_controls(args[2],args[3]))
        C.C.T.write_new(Path(args[4]),data);return review(data,P.sha(data))
    if len(args)==4 and args[0]=='statement':
        data=statement(C.C.read_input(args[1],MAX_ARCHIVE),args[2]);C.C.T.write_new(Path(args[3]),data)
        return {'statementSha256':P.sha(data),'servicesChanged':False}
    if len(args)==5 and args[0]=='verify':
        return authenticate(C.C.read_input(args[1],MAX_ARCHIVE),C.C.read_input(args[2],8192),
            C.C.read_input(args[3],64),C.C.read_input(args[4],4096))
    raise ValueError('build COMPONENT REPO COMMIT NEW_ARCHIVE; statement ARCHIVE KEY_SHA NEW_STATEMENT; verify ARCHIVE STATEMENT SIGNATURE TRUST')


if __name__=='__main__':
    try:print(P.encode(main(sys.argv[1:])).decode('ascii').strip())
    except Exception as error:
        print('Capture activation authorization refused: '+type(error).__name__,file=sys.stderr);sys.exit(1)
