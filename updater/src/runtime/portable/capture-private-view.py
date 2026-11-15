# SPDX-License-Identifier: GPL-3.0-only
"""Owned complete runtime/configuration view preparation. No unit writer/restart.

Staging attestation is not activation permission. These mount plans may only be
exercised by isolated path-reading fixtures until activation/graph/native proof.
"""
from contextlib import ExitStack,contextmanager
import fcntl
import importlib.util
import os
from pathlib import Path
import re
import stat
import sys

spec=importlib.util.spec_from_file_location('capture_view_stage',Path(__file__).with_name('capture-component-stage.py'))
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S);P=S.P
ROOT=Path('/var/lib/droiddeck-capture-views')
CONFIG=Path('/etc/droiddeck/runtime.json')
FILES={'intent.json','mounts.conf','complete.json','config'}
MODES=('default','cpu','gpu')


def token_name(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('Explicit fresh view token required')
    return value


def mounts(runtime,config):
    # Fixed target paths, protected canonical source roots and no settings that
    # reset another owner's lists, change commands, networking or credentials.
    if not re.fullmatch(r'/var/lib/droiddeck-capture/[a-f0-9]{64}/runtime',runtime):raise ValueError('Fixed complete staged runtime required')
    if not re.fullmatch(re.escape(str(ROOT))+r'/[a-f0-9]{32}/config',config):raise ValueError('Fixed complete private configuration view required')
    return ('[Service]\nPrivateMounts=yes\nBindReadOnlyPaths='+runtime+':/opt/droiddeck/runtime\n'
            'BindReadOnlyPaths='+config+':/etc/droiddeck\n').encode('ascii')


def policy(mode):
    if mode not in MODES:raise ValueError('Closed initial policy required')
    if mode=='gpu':return P.encode({'version':2,'preprocessing':'gpu','encoder':'vaapi'})
    return None if mode=='default' else P.encode({'version':1,'preprocessing':'cpu'})


def configuration(dirs):
    raw,gen=S.read_at(dirs.open(CONFIG.parent),CONFIG.name,16384,0o644)
    # Parse/normalization remains with existing worker API; preserve exact source
    # bytes and hash rather than generating new settings or copying services/TLS.
    return raw,gen


def record(component,token,mode,raw,identity):
    root=ROOT/token
    return {'schema':1,'kind':'owned-capture-private-view','token':token,'mode':mode,
        'componentSha256':component['report']['bundleSha256'],'componentReceiptSha256':component['receiptSha256'],
        'baseBinding':component['baseBinding'],'configurationSha256':P.sha(raw),
        'directoryIdentity':identity,'activationAllowed':False}


def prepare(digest,token,mode='default',*,checkpoint=lambda event:None):
    S.root_linux();P.digest(digest);token_name(token);initial=policy(mode)
    with S.hold_component(digest) as component,ExitStack() as stack:
        dirs=S.Dirs(stack);raw,config_generation=configuration(dirs)
        if P.sha(raw)!=component['baseBinding']['configSha256']:raise ValueError('Original configuration binding changed')
        parent=dirs.open(ROOT.parent)
        try:os.mkdir(ROOT.name,mode=0o700,dir_fd=parent);os.fsync(parent)
        except FileExistsError:pass
        root=S.private_dir(dirs,ROOT);fcntl.flock(root,fcntl.LOCK_EX|fcntl.LOCK_NB)
        os.mkdir(token,mode=0o700,dir_fd=root);os.fsync(root)
        view=ROOT/token;fd=S.private_dir(dirs,view);info=os.fstat(fd)
        intent=record(component,token,mode,raw,[info.st_dev,info.st_ino])
        S.write_new(fd,'intent.json',P.encode(intent));checkpoint('intent-written')
        os.mkdir('config',mode=0o700,dir_fd=fd);os.fsync(fd);config_fd=dirs.open(view/'config')
        S.write_new(config_fd,'runtime.json',raw,0o644);checkpoint('configuration-copied')
        if initial is not None:S.write_new(config_fd,'capture-policy.json',initial,0o644)
        os.fchmod(config_fd,0o555);os.fsync(config_fd)
        parent,name,handle,_=dirs.opened[view/'config'];info=os.fstat(handle)
        dirs.opened[view/'config']=(parent,name,handle,(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid))
        mapping=mounts(component['runtimeRoot'],str(view/'config'));S.write_new(fd,'mounts.conf',mapping)
        objects=inventory(dirs,view,intent,mapping,raw,initial)
        again,generation=configuration(dirs)
        if again!=raw or generation!=config_generation:raise ValueError('Original configuration generation changed')
        component['check']();dirs.check();checkpoint('before-complete')
        S.write_new(fd,'complete.json',P.encode({'schema':1,'intentSha256':P.sha(P.encode(intent)),'objects':objects}))
        checkpoint('complete-written')
        if set(os.listdir(fd))!=FILES:raise ValueError('Private view namespace changed')
        return report(intent)


def inventory(dirs,view,intent,mapping,raw,initial):
    fd=dirs.open(view);config_fd=dirs.open(view/'config')
    wanted={'runtime.json'} | ({'capture-policy.json'} if initial is not None else set())
    if set(os.listdir(config_fd))!=wanted or stat.S_IMODE(os.fstat(config_fd).st_mode)!=0o555:raise ValueError('Closed immutable private configuration required')
    result={'config':S.generation(os.fstat(config_fd))}
    for parent,name,data,mode,limit in [(fd,'intent.json',P.encode(intent),0o600,8192),(fd,'mounts.conf',mapping,0o600,4096),(config_fd,'runtime.json',raw,0o644,16384)]+([] if initial is None else [(config_fd,'capture-policy.json',initial,0o644,256)]):
        value,identity=S.read_at(parent,name,limit,mode)
        if value!=data:raise ValueError('Exact owned configuration/mapping bytes required')
        result[('config/' if parent==config_fd else '')+name]=identity
    return result


def report(intent):
    return {'kind':'owned-capture-private-view','token':intent['token'],'componentSha256':intent['componentSha256'],
        'configurationSha256':intent['configurationSha256'],'mode':intent['mode'],'activationAllowed':False,
        'unitChanged':False,'payloadExecuted':False,'readyForActivation':False}


@contextmanager
def hold_view(token):
    S.root_linux();token_name(token)
    # Read the root-owned identifier without adopting foreign directory metadata.
    with ExitStack() as initial:
        first=S.Dirs(initial);root=S.private_dir(first,ROOT)
        fd=S.private_dir(first,ROOT/token);raw,_=S.read_at(fd,'intent.json',8192,0o600)
        declared=P.document(raw);digest=declared['componentSha256'];P.digest(digest)
    # Fixed global lock order: original updater -> component -> view.
    with S.hold_component(digest) as component,ExitStack() as stack:
        dirs=S.Dirs(stack);root=S.private_dir(dirs,ROOT);fcntl.flock(root,fcntl.LOCK_EX|fcntl.LOCK_NB)
        view=ROOT/token;fd=S.private_dir(dirs,view)
        if set(os.listdir(fd))!=FILES:raise ValueError('Partial or foreign private view refused')
        raw,_=S.read_at(fd,'intent.json',8192,0o600);intent=P.document(raw)
        config,generation=configuration(dirs);s=os.fstat(fd)
        expected=record(component,token,intent['mode'],config,[s.st_dev,s.st_ino])
        if raw!=P.encode(expected):raise ValueError('Owned private view/base binding changed')
        initial_policy=policy(intent['mode']);mapping=mounts(component['runtimeRoot'],str(view/'config'))
        objects=inventory(dirs,view,intent,mapping,config,initial_policy)
        complete,complete_generation=S.read_at(fd,'complete.json',65536,0o600)
        if complete!=P.encode({'schema':1,'intentSha256':P.sha(raw),'objects':objects}):raise ValueError('Original completed view generations required')
        def check():
            component['check']();dirs.check()
            if set(os.listdir(fd))!=FILES:raise ValueError('Private view namespace changed')
            now,current=configuration(dirs)
            if now!=config or current!=generation:raise ValueError('Original configuration changed while view held')
            current_objects=inventory(dirs,view,intent,mapping,config,initial_policy)
            if current_objects!=objects or S.read_at(fd,'complete.json',65536,0o600)!=(complete,complete_generation):raise ValueError('Held private view changed')
        check()
        yield {'report':report(intent),'mapping':mapping,'configRoot':str(view/'config'),'runtimeRoot':component['runtimeRoot'],'check':check}
        check()


def inspect(token):
    with hold_view(token) as view:return dict(view['report'])


if __name__=='__main__':
    try:
        if len(sys.argv)==5 and sys.argv[1]=='prepare':value=prepare(*sys.argv[2:])
        elif len(sys.argv)==3 and sys.argv[1]=='inspect':value=inspect(sys.argv[2])
        else:raise ValueError('prepare COMPONENT_SHA256 NEW_TOKEN MODE or inspect TOKEN')
        print(P.encode(value).decode('ascii').strip())
    except Exception as error:
        print('Private view preparation refused; preserve partial state: '+type(error).__name__,file=sys.stderr);sys.exit(1)
