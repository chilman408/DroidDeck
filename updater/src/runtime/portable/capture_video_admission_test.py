# SPDX-License-Identifier: GPL-3.0-only
import copy
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest

def load(name):
    spec=importlib.util.spec_from_file_location('video_test_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

A=load('capture-video-admission')
F=load('effective_units_test')
NATIVE=sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()==0


def properties():
    service,unit=F.properties()
    service.update(Restart='always',RestartUSec=2000000,User='',Group='',PrivateMounts=False,BindPaths=[],BindReadOnlyPaths=[])
    for key in A.NS_FIELDS:
        service[key]=False if key.startswith('Private') or key=='MountAPIVFS' else ([] if key in ('ReadOnlyPaths','ReadWritePaths','InaccessiblePaths','TemporaryFileSystem','MountImages','ExtensionImages') else '')
    unit.update(FragmentPath='/run/systemd/transient/'+A.VIDEO,Transient=True,NeedDaemonReload=False)
    for key in ('BindsTo','Requisite','Conflicts','Before','OnFailure','JoinsNamespaceOf'):unit[key]=[]
    return service,unit


class Graph(unittest.TestCase):
    def setUp(self):
        self.service,self.unit=properties()
        self.base=A.project(self.service,self.unit)
        self.runtime='/var/lib/droiddeck-capture/'+'a'*64+'/runtime'
        self.config='/var/lib/droiddeck-capture-views/'+'b'*32+'/config'
    def mapped(self):
        result=copy.deepcopy(self.base);result.update(PrivateMounts=True,BindReadOnlyPaths=A.view_mounts(self.runtime,self.config),DropInPaths=[A.DROP]);return result
    def test_exact_baseline_and_complete_owned_mapping(self):
        self.assertTrue(A.require_graph(self.base,self.base))
        self.assertTrue(A.require_graph(self.base,self.mapped(),runtime=self.runtime,config=self.config))
        self.assertIs(A.require_baseline(self.base),self.base)
    def test_no_generic_dropin_or_partial_mapping_exception(self):
        for changes in ({'DropInPaths':['/run/systemd/system/foreign.conf']},{'PrivateMounts':False},{'BindReadOnlyPaths':[]},
                {'BindPaths':[[self.runtime,'/opt/droiddeck/runtime',False,16384]]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                A.require_graph(self.base,{**self.mapped(),**changes},runtime=self.runtime,config=self.config)
        with self.assertRaises(ValueError):A.require_graph(self.base,self.mapped(),runtime=self.runtime)
        with self.assertRaises(ValueError):A.require_graph(self.base,self.mapped())
    def test_unchanged_command_environment_identity_and_sandbox_required(self):
        for field in ('ExecStartSha256','ExecStopPostSha256','EnvironmentSha256','EnvironmentFilesSha256','NamespaceSha256','User','Group','Restart','KillMode'):
            changed=self.mapped();changed[field]='generated foreign field'
            with self.subTest(field=field),self.assertRaises(ValueError):
                A.require_graph(self.base,changed,runtime=self.runtime,config=self.config)
    def test_dependency_edges_only_add_existing_root_mount(self):
        changed=self.mapped();changed['Requires']+=['-.mount'];changed['After']+=['-.mount']
        self.assertTrue(A.require_graph(self.base,changed,runtime=self.runtime,config=self.config))
        for field in ('Requires','After','Wants','BindsTo','OnFailure','Before'):
            changed=self.mapped();changed[field]+=['foreign.service']
            with self.subTest(field=field),self.assertRaises(ValueError):
                A.require_graph(self.base,changed,runtime=self.runtime,config=self.config)
        changed=self.mapped();changed['After']=[]
        with self.assertRaises(ValueError):A.require_graph(self.base,changed,runtime=self.runtime,config=self.config)
    def test_missing_extra_and_wrong_graph_types_refused(self):
        changed=self.mapped();del changed['NamespaceSha256']
        with self.assertRaises(ValueError):A.require_graph(self.base,changed,runtime=self.runtime,config=self.config)
        changed=self.mapped();changed['unknown']=True
        with self.assertRaises(ValueError):A.require_graph(self.base,changed,runtime=self.runtime,config=self.config)
        for key,value in [('RestartUSec',True),('BindPaths',[['/source','/target',0,16384]]),('BindReadOnlyPaths',[['a','b',False,'rbind']])]:
            service=copy.deepcopy(self.service);service[key]=value
            with self.assertRaises((ValueError,TypeError)):A.project(service,self.unit)
    def test_projection_does_not_emit_environment_or_command_values(self):
        text=json.dumps(self.base)
        self.assertNotIn('generated-sentinel',text);self.assertNotIn('generated-private-argument',text)
    def test_unknown_recursive_flags_ignored_missing_or_destinations_refused(self):
        for index,field,value in [(0,2,True),(0,3,0),(0,1,'/opt/droiddeck'),(1,0,'/foreign')]:
            changed=self.mapped();changed['BindReadOnlyPaths'][index][field]=value
            with self.assertRaises(ValueError):A.require_graph(self.base,changed,runtime=self.runtime,config=self.config)
    def test_paths_reject_relative_traversal_and_noncanonical_tokens(self):
        for runtime,config in [('/tmp/runtime',self.config),(self.runtime,'/tmp/config'),(self.runtime+'/../runtime',self.config),(self.runtime,self.config+'\n')]:
            with self.assertRaises(ValueError):A.view_mounts(runtime,config)
    def test_dynamic_snapshot_uses_service_cgroup_not_flattened_unit(self):
        service={'ControlGroup':'/system.slice/generated.service','MainPID':42,'NRestarts':0}
        unit={'ActiveState':'active','SubState':'running','InvocationID':bytes.fromhex('a'*32)}
        record=A.state_record(service,unit)
        self.assertEqual(record['ControlGroup'],service['ControlGroup']);self.assertEqual(record['MainPID'],42)
        with self.assertRaises((KeyError,ValueError)):A.state_record({**service,'MainPID':True},unit)
    def test_pam_compositor_requires_exact_local_owned_logind_session(self):
        service={'PAMName':'droiddeck'}
        session={'Id':'c3','Scope':'session-c3.scope','User':[1000,'/generated/user'],'Name':'fixtureuser','Service':'droiddeck',
            'Remote':False,'State':'online','Leader':42,'Class':'user','Type':'wayland'}
        result,group=A.pam_session(service,session,'fixtureuser',1000)
        self.assertEqual(group,'/user.slice/user-1000.slice/session-c3.scope')
        for key,value in [('Service','sshd'),('Remote',True),('Name','foreign'),('User',[1001,'/generated/user']),('Scope','foreign.scope'),('State','closing')]:
            with self.subTest(key=key),self.assertRaises(ValueError):A.pam_session(service,{**session,key:value},'fixtureuser',1000)
    def test_native_lease_schema_is_closed_without_duplicate_or_private_inputs(self):
        raw=b'{"version":1,"lease":"'+b'a'*32+b'"}'
        self.assertEqual(A.native_owner(raw),'a'*32)
        for value in (raw.replace(b'"version":1',b'"version":true'),raw.replace(b'"version":1',b'"version":1,"version":1'),raw.replace(b'a'*32,b'bad'),b'{}'):
            with self.assertRaises(ValueError):A.native_owner(value)
    def test_native_role_requires_same_owner_and_exact_video_role(self):
        env={'TESLA_WAYDROID_RUNTIME_OWNER':'a'*32,'TESLA_WAYDROID_RUNTIME_ROLE':'video'}
        self.assertTrue(A.require_native_role('a'*32,env))
        for changed in ({},{**env,'TESLA_WAYDROID_RUNTIME_OWNER':'b'*32},{**env,'TESLA_WAYDROID_RUNTIME_ROLE':'audio'}):
            with self.assertRaises(ValueError):A.require_native_role('a'*32,changed)
    def test_crossboot_environment_normalizes_only_verified_native_nonce(self):
        one=['TESLA_WAYDROID_RUNTIME_OWNER='+'a'*32,'TESLA_WAYDROID_RUNTIME_ROLE=video','DROIDDECK_CAPTURE=cpu']
        two=[v.replace('a'*32,'b'*32) for v in one]
        self.assertEqual(A.stable_environment(one),A.stable_environment(two))
        self.assertNotEqual(A.E.digest(A.E.strings(one)),A.E.digest(A.E.strings(two)))
        self.assertNotEqual(A.stable_environment(one),A.stable_environment(two+['OTHER=changed']))
        with self.assertRaises(ValueError):A.stable_environment(['TESLA_WAYDROID_RUNTIME_OWNER=bad'])
    def test_baseline_never_adopts_existing_override(self):
        for field,value in [('DropInPaths',[A.DROP]),('PrivateMounts',True),('Transient',False),('NeedDaemonReload',True),('RestartUSec',200000)]:
            with self.assertRaises(ValueError):A.require_baseline({**self.base,field:value})


@unittest.skipUnless(NATIVE,'Root Linux held-file metadata tests')
class Held(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory(prefix='generated-native-lease-');self.addCleanup(temp.cleanup)
        root=Path(temp.name);root.chmod(0o700);file=root/'lease.json';file.write_bytes(b'{"version":1,"lease":"' + b'a'*32 + b'"}\n');file.chmod(0o600)
        return root,file
    def test_descriptor_and_path_generations_reject_same_bytes_replacement(self):
        root,file=self.fixture()
        with ExitStack() as stack:
            held=A.HeldFile(stack,file,0o600,512);held.check()
            raw=file.read_bytes();file.rename(root/'old');file.write_bytes(raw);file.chmod(0o600)
            with self.assertRaises(ValueError):held.check()
    def test_links_unsafe_modes_and_owner_refused(self):
        for kind in ('mode','hardlink','symlink','owner'):
            root,file=self.fixture()
            if kind=='mode':file.chmod(0o644)
            if kind=='hardlink':os.link(file,root/'extra')
            if kind=='symlink':file.rename(root/'old');file.symlink_to(root/'old')
            if kind=='owner':os.chown(file,1000,1000)
            with self.subTest(kind=kind),ExitStack() as stack,self.assertRaises((OSError,ValueError)):
                A.HeldFile(stack,file,0o600,512)
    def test_modified_held_directory_refused(self):
        root,file=self.fixture()
        with ExitStack() as stack:
            held=A.HeldFile(stack,file,0o600,512);root.chmod(0o755)
            with self.assertRaises(ValueError):held.check()


if __name__=='__main__':unittest.main(verbosity=2)
