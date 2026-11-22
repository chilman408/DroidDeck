# SPDX-License-Identifier: GPL-3.0-only
"""Pure RC18 aggregate inventory/authorization policy. Never import or install payloads."""
import argparse,ast,hashlib,io,json,os,re,stat,subprocess,tarfile,zipfile
from pathlib import Path,PurePosixPath

PRODUCT_VERSION='0.1.1'
PREFIX='droidmate-rc18-release/'
MANIFEST=PREFIX+'manifest.json'
CORE='1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
COMPONENTS=(33423360,'30e86c1efebbf1ed4203190f8c5e6f4f9a88b7edcf40ccf2a3e785cfb280fade')
FRONTEND_PIN='754e5d2da9a03b38d8c5943c556109e50a5f00441c777bf62ce2306f6a2e5826'
FRONTEND_COUNT=33
LICENSE_PIN='3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986'
FROZEN={
 'runtime/portable/capture-component-staging.statement.json':'cec4f524145cad2042145a844304051a5bac1dd069b29f662db8d54bfa8ee202',
 'runtime/portable/capture-component-staging.sig':'371f9db5fcf1484f4adf63ee94fd9fa446196009c4259dfae9eab1cf793aa770',
 'tools/update-installer-entry.py':'48ae63a3deb49022ec7065c0a17ea836712eef97080952ba22aed96948bcd7c5',
 'tools/package-rc18-components.py':'dbcb1eb166a504410ba561eea9f5cd7135bad8eae494e4aaef99f6b651ac0d9d',
 'tools/aggregate-browser-view.py':'5cd776b0bee404760b00ba9fb34dff3047bb1c627f7fd9163753f4883ccb9ad2',
 'runtime/portable/aggregate-gateway.js':'037856026e0c90b1d80452970c688768eaaadf2908124c3fc9a4fce37e842223',
 'tools/legacy-video-namespace.py':'c711c34401a22db7f26b230f6f2a446c6ff7d569ef2639d7d56ef4842515d012',
 'tools/legacy-gpu-preflight.py':'e0536c2809e13a5506e9f65401f8a449ec3b9eb95c5a963bd2cf000c950887c6',
 'tools/enroll-capture-control.py':'44252a64a419cdfac80f7f8c898a17ef9ee1a9a5b82896a93a3222478a9502c1',
 'runtime/portable/capture-control-bootstrap.py':'22c52ff53faeae67716171d6d15e5dfc79ff11b9268fe59037406e651961f707',
}
SOURCES=tuple(sorted((*FROZEN,'COPYING','docs/RC18-INSTALL.md','docs/RC18-RELEASE-NOTES.md','tools/aggregate-release.py',
 'tools/aggregate-sign-inputs.py','tools/aggregate-installer-entry.py','tools/aggregate_retained_entry_test.py','tools/aggregate-install.py',
 'tools/aggregate-legacy-video.py','tools/aggregate-legacy-runtime.py','tools/aggregate_legacy_runtime_test.py',
 'tools/aggregate-legacy-backend.py','tools/aggregate_legacy_backend_test.py','tools/aggregate-portable-capture.py','tools/aggregate-fresh-install.py',
 'tools/aggregate-enroll.py','tools/aggregate_enroll_test.py',
 'tools/aggregate-runtime.py','tools/aggregate_runtime_test.py','tools/aggregate_gateway_config_test.py','tools/aggregate-supervisor.py','tools/aggregate_supervisor_test.py',
 'tools/aggregate-material.py','tools/aggregate_material_test.py','tools/aggregate_portable_capture_test.py',
 'tools/aggregate-build-toolchain.py','tools/aggregate_build_toolchain_test.py','tools/aggregate_fresh_install_test.py','tools/aggregate_release_test.py','tools/aggregate_browser_view_test.py',
 'runtime/portable/aggregate-gateway.test.js','tools/renew-local-certificate.py','tools/renew_local_certificate_test.py')))
CONTRACT={'schema':1,'scope':'preserving-aggregate-install','layouts':['portable','recognized-legacy'],
 'originalCore':'immutable','androidData':'preserve','accountsPinsTls':'preserve',
 'runtimeView':'service-private','gpu':'preferred-with-cpu-failsafe','healthyExpiry':False,
 'unraidChanges':'selected-vm-private-profile-only','sharedQemuLibrariesPermissions':'preserve',
 'virtio3dAndVnc':'retain','recovery':'owned-durable-undo'}
MAX_FILE=40*1024**2
MAX_ARCHIVE=48*1024**2
MAX_SOURCE=1024**2

def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')
def unique(rows):
    result={}
    for k,v in rows:
        if k in result:raise ValueError('Duplicate aggregate metadata key')
        result[k]=v
    return result
def invalid_constant(value):raise ValueError('Nonfinite aggregate metadata refused')
def document(raw,*,canonical=True):
    value=json.loads(raw,object_pairs_hook=unique,parse_constant=invalid_constant)
    if canonical and encode(value)!=raw:raise ValueError('Canonical aggregate metadata required')
    return value
def revision(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{40}',value):raise ValueError('Immutable source revision required')
    return value
def digest(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('Canonical SHA256 required')
    return value
def records(files):return [{'path':n,'size':len(b),'sha256':sha(b)} for n,b in sorted(files.items())]
def source_checkpoint(sources,rev):
    return encode({'schema':1,'kind':'droidmate-aggregate-source-checkpoint','sourceRevision':revision(rev),'files':records(sources)})
def name(value):
    if (type(value) is not str or not re.fullmatch('[A-Za-z0-9_./@+-]{1,240}',value)
            or PurePosixPath(value).is_absolute() or str(PurePosixPath(value))!=value
            or any(p in ('.','..','.git','.ssh','.config','.env') for p in value.split('/'))):
        raise ValueError('Closed canonical public path required')
    return value
def public_sources(sources):
    if type(sources) is not dict or set(sources)!=set(SOURCES):raise ValueError('Complete closed aggregate source set required')
    for n,b in sources.items():
        if type(b) is not bytes or not 0<len(b)<=MAX_SOURCE:raise ValueError('Bounded public source required')
        if (any(prefix in b for prefix in (b'Plbal'+b'al',b'Plbal'+b'bl'))
            or re.search(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s*[A-Za-z0-9+/=]{16,}',b)
            or re.search(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})\b',b)):
            raise ValueError('Private source material refused')
        if n.endswith('.py'):ast.parse(b,filename=n)
    if sha(sources['COPYING'])!=LICENSE_PIN:raise ValueError('Original GPL license required')
    if any(sha(sources[n])!=pin for n,pin in FROZEN.items()):raise ValueError('Original qualified integration sources required')
def frontend_files(frontend):
    if type(frontend) is not dict or not all(type(b) is bytes and 0<len(b)<=8*1024**2 for b in frontend.values()):
        raise ValueError('Bounded public frontend files required')
    raw=frontend.get('bundle-manifest.json')
    if raw is None or sha(raw)!=FRONTEND_PIN:raise ValueError('Original prepared frontend manifest required')
    meta=document(raw,canonical=False)
    rows=meta.get('files') if type(meta) is dict else None
    if type(rows) is not list or len(rows)!=FRONTEND_COUNT:raise ValueError('Complete prepared frontend required')
    files={n:b for n,b in frontend.items() if n!='bundle-manifest.json'}
    for n in files:name(n)
    if rows!=records(files):raise ValueError('Original frontend inventory and every hash required')
    return frontend
def entries(components,frontend,sources,rev):
    revision(rev);public_sources(sources);frontend_files(frontend)
    if type(components) is not bytes or (len(components),sha(components))!=COMPONENTS:
        raise ValueError('Original immutable QU component distribution required')
    files={PREFIX+'components.tar':components,
           **{PREFIX+'frontend/'+n:b for n,b in frontend.items()},
           **{PREFIX+'source/'+n:b for n,b in sources.items()}}
    meta={'schema':1,'kind':'droidmate-rc18-aggregate-release','aggregateRelease':'RC18','coreRuntimeVersion':'0.1.1-rc.20',
          'compatibleReleaseSha256':CORE,'sourceRevision':rev,'componentsSha256':sha(components),
          'frontendManifestSha256':sha(frontend['bundle-manifest.json']),'sourceInventorySha256':sha(encode(records(sources))),
          'contract':CONTRACT,'files':records(files)}
    files[MANIFEST]=encode(meta)
    return files
def archive(files):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:',format=tarfile.USTAR_FORMAT) as t:
        for n,b in sorted(files.items()):
            m=tarfile.TarInfo(n);m.size=len(b);m.mode=0o644;t.addfile(m,io.BytesIO(b))
    return output.getvalue()
def build(components,frontend,sources,rev):
    raw=archive(entries(components,frontend,sources,rev))
    if len(raw)>MAX_ARCHIVE:raise ValueError('Aggregate bound exceeded')
    return raw
def unpack(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_ARCHIVE:raise ValueError('Bounded aggregate archive required')
    files={};total=0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as t:
            for m in t:
                name(m.name)
                if (not m.name.startswith(PREFIX) or m.name in files or m.type!=tarfile.REGTYPE or m.mode!=0o644
                    or m.pax_headers or m.sparse is not None or m.linkname or m.uid or m.gid or m.uname or m.gname or m.mtime
                    or not 0<m.size<=MAX_FILE or len(files)>=512):raise ValueError('Closed canonical regular members required')
                total+=m.size
                if total>MAX_ARCHIVE:raise ValueError('Expanded aggregate bound exceeded')
                b=t.extractfile(m).read(MAX_FILE+1)
                if len(b)!=m.size:raise ValueError('Complete member bytes required')
                files[m.name]=b
    except (tarfile.TarError,EOFError,OSError) as error:raise ValueError('Invalid aggregate archive') from error
    if archive(files)!=raw:raise ValueError('Exact canonical complete aggregate bytes required')
    return files
def review(raw):
    files=unpack(raw)
    if MANIFEST not in files:raise ValueError('Aggregate manifest required')
    meta=document(files[MANIFEST])
    if type(meta) is not dict:raise ValueError('Closed aggregate manifest required')
    rev=revision(meta.get('sourceRevision'))
    front={n[len(PREFIX+'frontend/'):]:b for n,b in files.items() if n.startswith(PREFIX+'frontend/')}
    sources={n[len(PREFIX+'source/'):]:b for n,b in files.items() if n.startswith(PREFIX+'source/')}
    try:expected=entries(files[PREFIX+'components.tar'],front,sources,rev)
    except KeyError as error:raise ValueError('Complete aggregate inputs required') from error
    if files!=expected:raise ValueError('Original complete aggregate inventory and contract required')
    return {'kind':'droidmate-aggregate-review','aggregateRelease':'RC18','coreRuntimeVersion':'0.1.1-rc.20',
            'compatibleReleaseSha256':CORE,'packageSha256':sha(raw),'manifestSha256':sha(files[MANIFEST]),
            'sourceRevision':rev,'sourceInventorySha256':meta['sourceInventorySha256'],'componentsSha256':meta['componentsSha256'],
            'sourceCheckpointSha256':sha(source_checkpoint(sources,rev)),
            'frontendManifestSha256':FRONTEND_PIN,'frontendFiles':FRONTEND_COUNT,'sourceFiles':len(sources),
            'payloadExecuted':False,'readyForPublication':False}
def statement(raw,key):
    r=review(raw)
    return encode({'schema':1,'kind':'droidmate-aggregate-release-authorization','channel':'pilot',
        'aggregateRelease':'RC18','coreRuntimeVersion':'0.1.1-rc.20','compatibleReleaseSha256':CORE,
        'activationAllowed':True,'keySha256':digest(key),'packageSha256':r['packageSha256'],'manifestSha256':r['manifestSha256'],
        'sourceRevision':r['sourceRevision'],'sourceInventorySha256':r['sourceInventorySha256'],'contractSha256':sha(encode(CONTRACT))})
def review_signing_inputs(raw,signed_statement,key):
    if type(signed_statement) is not bytes or not 0<len(signed_statement)<=8192:raise ValueError('Bounded separate aggregate statement required')
    document(signed_statement)
    if statement(raw,key)!=signed_statement:raise ValueError('Separate exact aggregate authorization domain required')
    return {'kind':'droidmate-aggregate-signing-inputs','keySha256':digest(key),'statementSha256':sha(signed_statement),
            'packageSha256':sha(raw),'payloadExecuted':False,'servicesChanged':False}

def installer(raw,signed_statement,signature):
    """Assemble supplied signature bytes; Linux entry must verify before use."""
    review_signing_inputs(raw,signed_statement,'4e0cb3fe1781c97522154c0a33d9678873cfb837808331ba218ad9c70425b34f')
    if type(signature) is not bytes or len(signature)!=64:raise ValueError('Supplied64byte signature required')
    main=unpack(raw)[PREFIX+'source/tools/aggregate-installer-entry.py']
    entries={'__main__.py':main,'release.tar':raw,'statement.json':signed_statement,'signature.bin':signature}
    output=io.BytesIO()
    # Do not import the packaged entry to assemble it. Canonical ZIP is the
    # public four-member protocol shared with its independent verifier.
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as z:
        for n,b in sorted(entries.items()):
            row=zipfile.ZipInfo(n,(1980,1,1,0,0,0));row.create_system=3;row.external_attr=0o100644<<16;row.compress_type=zipfile.ZIP_STORED
            z.writestr(row,b)
    return output.getvalue()

def primitives():
    # Reuse qualified binary/Git primitives, never payload code. Authenticate
    # the local public helper snapshot before compiling it.
    path=Path(__file__).with_name('package-rc18-components.py')
    raw=path.read_bytes().replace(b'\r\n',b'\n')
    if sha(raw)!=FROZEN['tools/package-rc18-components.py']:raise ValueError('Original qualified public primitives required')
    scope={'__name__':'trusted_rc18_component_primitives','__file__':str(path)}
    exec(compile(raw,str(path),'exec'),scope)
    scope['SOURCES']=SOURCES;scope['validate_sources']=public_sources
    return scope
def git_sources(root,rev):return primitives()['git_sources'](root,rev)
def source_tree(root,rev,pin):
    revision(rev);digest(pin);p=primitives()
    sources={n:p['read'](Path(root)/n,MAX_SOURCE) for n in SOURCES}
    public_sources(sources)
    if sha(source_checkpoint(sources,rev))!=pin:raise ValueError('External aggregate source checkpoint/revision required')
    return sources
def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='operation',required=True)
    inspect=sub.add_parser('review');inspect.add_argument('archive',type=Path);inspect.add_argument('sha256')
    capsule=sub.add_parser('installer')
    for n in ('archive','statement','signature','output'):capsule.add_argument(n,type=Path)
    make=sub.add_parser('build');group=make.add_mutually_exclusive_group(required=True)
    group.add_argument('--source-root',type=Path);group.add_argument('--checkpoint',type=Path)
    make.add_argument('--source-checkpoint-sha256');make.add_argument('--revision',required=True)
    make.add_argument('--components',type=Path,required=True);make.add_argument('--frontend',type=Path,required=True)
    make.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();p=primitives()
    if args.operation=='installer':
        raw=installer(p['read'](args.archive,MAX_ARCHIVE),p['read'](args.statement,8192),p['read'](args.signature,64))
        p['write_new'](args.output,raw)
        print(encode({'kind':'droidmate-aggregate-capsule-assembled','sha256':sha(raw),'signatureVerified':False,'readyForPublication':False}).decode('ascii').strip());return
    if args.operation=='review':
        raw=p['read'](args.archive,MAX_ARCHIVE)
        if sha(raw)!=digest(args.sha256):raise ValueError('External aggregate archive digest required')
    else:
        if args.source_root and args.source_checkpoint_sha256:raise ValueError('Source checkpoint belongs only to Git-free mode')
        sources=git_sources(args.source_root,args.revision) if args.source_root else source_tree(args.checkpoint,args.revision,args.source_checkpoint_sha256)
        if not stat.S_ISDIR(args.frontend.lstat().st_mode):raise ValueError('Real prepared frontend directory required')
        frontend={}
        for current,dirs,names in os.walk(args.frontend,followlinks=False):
            if any(not stat.S_ISDIR((Path(current)/n).lstat().st_mode) for n in dirs):raise ValueError('Real public frontend directories required')
            for n in names:
                target=Path(current)/n;rel=target.relative_to(args.frontend).as_posix();name(rel)
                frontend[rel]=p['read'](target,8*1024**2)
                if len(frontend)>64:raise ValueError('Closed frontend count exceeded')
        raw=build(p['read'](args.components,MAX_FILE),frontend,sources,args.revision)
        review(raw);p['write_new'](args.output,raw)
    print(encode(review(raw)).decode('ascii').strip())

if __name__=='__main__':main()
