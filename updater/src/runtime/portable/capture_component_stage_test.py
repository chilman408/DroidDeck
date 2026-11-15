# SPDX-License-Identifier: GPL-3.0-only
"""Root Linux generated-slot tests; production release lease is never replaced by CLI."""
import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

NATIVE=sys.platform=='linux' and os.geteuid()==0 if hasattr(os,'geteuid') else False
INPUTS=os.environ.get('DROIDDECK_CAPTURE_STAGE_TEST_INPUTS')


@unittest.skipUnless(NATIVE and INPUTS,'Root Linux explicit PUBLIC signed component fixture required')
class Slots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('stage_tested',Path(__file__).with_name('capture-component-stage.py'))
        cls.S=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.S)
        cls.inputs=Path(INPUTS)
        cls.expected=cls.S.P.sha((cls.inputs/'bundle.tar').read_bytes())

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='generated-capture-slot-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.calls=0;self.binding={'sourceSha256':'a'*64,'configSha256':'b'*64,'releaseSha256':'c'*64}
        report={'kind':'verified-installed-pilot-release','signedReleaseVerified':True,'installedCodeOwnershipVerified':True,
                'updateLockShared':True,'signedBundleSha256':self.S.A.BASE_RELEASE,'binding':self.binding,'servicesChanged':False}
        self.report=report
        @contextmanager
        def lease():
            def check():
                self.calls+=1
                return self.report
            yield {'report':self.report,'check':check}
            check()
        self.addCleanup(patch.stopall)
        patch.object(self.S,'ROOT',self.root/'components').start()
        patch.object(self.S,'TRUST',self.inputs/'trust.json').start()
        patch.object(self.S.R,'hold_release',lease).start()

    def stage(self,**kwargs):
        return self.S.stage(*(self.inputs/n for n in ('bundle.tar','statement.json','signature.bin')),**kwargs)

    def test_complete_signed_241_file_slot_and_retained_generations(self):
        report=self.stage();self.assertEqual(report['files'],241);self.assertIs(report['activationAllowed'],False)
        self.assertGreaterEqual(self.calls,2)
        checked=self.S.inspect(self.expected);self.assertTrue(checked['releaseStatementAuthenticated'])
        self.assertIs(checked['readyForActivation'],False)
        slot=self.S.ROOT/self.expected
        self.assertEqual((slot/'runtime').stat().st_mode&0o777,0o555)
        self.assertEqual((slot/'runtime/portable/capture-policy.js').stat().st_mode&0o777,0o444)

    def test_no_replay_or_same_byte_slot_adoption(self):
        self.stage();before=(self.S.ROOT/self.expected/'intent.json').read_bytes()
        with self.assertRaises(FileExistsError):self.stage()
        self.assertEqual((self.S.ROOT/self.expected/'intent.json').read_bytes(),before)

    def test_same_bytes_replacement_and_extra_directory_refused(self):
        self.stage();slot=self.S.ROOT/self.expected
        file=slot/'runtime/portable/capture-policy.js';data=file.read_bytes();file.unlink();file.write_bytes(data);file.chmod(0o444)
        with self.assertRaises(ValueError):self.S.inspect(self.expected)

    def test_unlisted_file_and_linked_payload_refused(self):
        self.stage();slot=self.S.ROOT/self.expected
        (slot/'runtime/portable/foreign.js').write_bytes(b'generated unexpected sentinel')
        with self.assertRaises(ValueError):self.S.inspect(self.expected)
        (slot/'runtime/portable/foreign.js').unlink()
        file=slot/'runtime/portable/capture-policy.js';os.link(file,self.root/'extra-hardlink')
        with self.assertRaises(ValueError):self.S.inspect(self.expected)

    def test_wrong_base_is_refused_before_new_namespace(self):
        self.report['signedBundleSha256']='0'*64
        with self.assertRaises(ValueError):self.stage()
        self.assertFalse(self.S.ROOT.exists())

    def test_five_process_death_boundaries_never_activate_or_replay(self):
        initial=self.S.ROOT
        for index,event in enumerate(('intent-written','first-payload-written','files-staged','before-complete','complete-written')):
            with self.subTest(event=event):
                self.S.ROOT=self.root/('death-'+str(index))
                pid=os.fork()
                if pid==0:
                    def stop(name):
                        if name==event:os._exit(73)
                    try:self.stage(checkpoint=stop)
                    except BaseException:os._exit(91)
                    os._exit(92)
                _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),73)
                if event=='complete-written':
                    self.assertIs(self.S.inspect(self.expected)['activationAllowed'],False)
                else:
                    with self.assertRaises(ValueError):self.S.inspect(self.expected)
                with self.assertRaises(FileExistsError):self.stage()
        self.S.ROOT=initial


if __name__=='__main__':unittest.main(verbosity=2)
