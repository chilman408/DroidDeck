# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
from pathlib import Path
import unittest
from contextlib import nullcontext
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('update_ui_tested', Path(__file__).with_name('update-ui.py'))
U = importlib.util.module_from_spec(spec); spec.loader.exec_module(U)


def release(version='0.1.1-rc.7', **changes):
    name = 'droiddeck-' + version + '-update.pyz'
    row = {'draft': False, 'id': 123, 'tag_name': 'v' + version, 'assets': [{'name': name, 'state': 'uploaded',
        'browser_download_url': U.DOWNLOAD + 'v' + version + '/' + name, 'size': 1000, 'digest': 'sha256:' + 'a' * 64}]}
    row.update(changes); return row


class Updates(unittest.TestCase):
    def test_catalog_selects_newest_signed_updater_kind_and_ignores_clean_installer(self):
        rows = [release('0.1.1-rc.6'), release(), release('0.1.1-rc.8', draft=True), release('0.1.0-rc.2', assets=[])]
        self.assertEqual(U.select_offer(json.dumps(rows), '0.1.0-rc.2')['version'], '0.1.1-rc.7')
        self.assertIsNone(U.select_offer(json.dumps(rows), '0.1.1-rc.7'))

    def test_foreign_urls_duplicate_assets_missing_digest_and_huge_assets_refuse(self):
        for change in ({'browser_download_url': 'https://evil.invalid/x'}, {'digest': None}, {'size': 2**30}, {'state': 'new'}):
            row = release(); row['assets'][0].update(change)
            self.assertIsNone(U.select_offer(json.dumps([row]), '0.1.0-rc.2'))
        row = release(); row['assets'] *= 2
        self.assertIsNone(U.select_offer(json.dumps([row]), '0.1.0-rc.2'))

    def test_redirects_never_admit_http_credentials_ports_or_unrelated_hosts(self):
        for url in ('http://github.com/x', 'https://github.com.evil/x', 'https://u@github.com/x', 'https://github.com:8443/x',
                    'https://127.0.0.1/x', 'file:///etc/passwd', 'https://api.github.com/x#secret'):
            with self.assertRaises(ValueError): U.checked_url(url)
        self.assertEqual(U.checked_url(U.API), U.API)

    def test_semver_compares_rc_numerically_and_stable_after_candidate(self):
        self.assertGreater(U.version('0.1.1-rc.10'), U.version('0.1.1-rc.9'))
        self.assertGreater(U.version('0.1.1'), U.version('0.1.1-rc.10'))
        for value in ('rc.8', '1.2.3-rc.0', '1.2.3;id', None):
            with self.assertRaises(ValueError): U.version(value)

    def harness(self, job=None, active=False):
        self.records = {'job.json': job, 'offer.json': {'version': '0.1.1-rc.7', 'token': 'b'*32}}
        for item in (patch.object(U, 'locked', nullcontext), patch.object(U, 'job_active', return_value=active),
                     patch.object(U, 'history_floor', side_effect=lambda state, installed: installed),
                     patch.object(U, 'installed_version', return_value='0.1.0-rc.2'),
                     patch.object(U, 'inspection', return_value={'current':'d'*64, 'phases':['healthy.json']}),
                     patch.object(U, 'read', side_effect=lambda key, default=None: self.records.get(key, default)),
                     patch.object(U, 'save', side_effect=lambda key, value: self.records.__setitem__(key, value))):
            item.start(); self.addCleanup(item.stop)

    def test_job_is_persisted_before_independent_systemd_dispatch(self):
        self.harness()
        def execute(args):
            self.assertEqual(self.records['job.json']['phase'], 'queued')
            self.assertIn('run-job', args); self.assertNotIn('shell', args)
        with patch.object(U, 'command', side_effect=execute) as execute:
            U.dispatch({'action':'install','token':'b'*32,'requestId':'c'*32})
            self.assertEqual(execute.call_count, 1)

    def test_identical_request_retry_is_read_only_not_another_install(self):
        self.harness({'id':'c'*32,'token':'b'*32,'action':'install','phase':'complete','target':'0.1.1-rc.7'})
        with patch.object(U, 'command') as execute:
            U.dispatch({'action':'install','token':'b'*32,'requestId':'c'*32}); execute.assert_not_called()
            with self.assertRaises(ValueError): U.dispatch({'action':'rollback','token':'b'*32,'requestId':'c'*32})

    def test_interrupted_job_cannot_be_replaced_while_original_recovery_pending(self):
        self.harness({'id':'c'*32,'token':'b'*32,'action':'install','phase':'installing','target':'0.1.1-rc.7'})
        self.assertEqual(U.snapshot()['job']['phase'], 'interrupted')
        with patch.object(U, 'inspection', return_value={'current':'d'*64,'phases':['apply-intent.json']}), patch.object(U, 'command') as execute:
            with self.assertRaises(ValueError): U.dispatch({'action':'install','token':'b'*32,'requestId':'e'*32})
            execute.assert_not_called()

    def test_lost_job_process_reports_only_original_verified_completion(self):
        self.harness({'id':'c'*32,'token':'b'*32,'action':'install','phase':'installing','target':'0.1.1-rc.7','digest':'d'*64})
        self.assertEqual(U.snapshot()['job']['phase'], 'interrupted')
        with patch.object(U, 'installed_version', return_value='0.1.1-rc.7'):
            self.assertEqual(U.snapshot()['job']['phase'], 'complete')
        with patch.object(U, 'inspection', return_value={'current':'d'*64,'phases':['rolled-back.json']}):
            self.assertEqual(U.snapshot()['job']['phase'], 'failed')

    def test_progress_does_not_compete_with_original_apply_lock(self):
        self.harness({'id':'c'*32,'token':'b'*32,'action':'install','phase':'installing','target':'0.1.1-rc.7'},active=True)
        with patch.object(U, 'inspection', side_effect=BlockingIOError('Original apply lock held')) as inspect:
            self.assertEqual(U.snapshot()['job']['phase'],'installing')
            inspect.assert_not_called()

    def test_rolled_back_version_is_consumed_even_when_installed_version_is_baseline(self):
        statement={'bundleSha256':'d'*64,'target':{'version':'0.1.1-rc.7'}}
        with patch.object(U,'protected',return_value=json.dumps(statement).encode()):
            self.assertEqual(U.history_floor({'current':'d'*64},'0.1.0-rc.2'),'0.1.1-rc.7')
            self.assertIsNone(U.select_offer(json.dumps([release()]),U.history_floor({'current':'d'*64},'0.1.0-rc.2')))

    def test_rollback_job_waits_for_original_verifier_after_type_exec_startup(self):
        job={'id':'c'*32,'token':'d'*64,'action':'rollback','phase':'queued','target':'0.1.0-rc.2','offer':None}
        self.harness(job)
        states=[{'current':'d'*64,'phases':['healthy.json']},
                {'current':'d'*64,'phases':['healthy.json','rollback-request.json']},
                {'current':'d'*64,'phases':['healthy.json','rolled-back.json']}]
        with patch.object(U,'locked',side_effect=lambda **_:nullcontext()), patch.object(U,'inspection',side_effect=states), \
             patch.object(U,'command') as execute, patch.object(U.time,'sleep') as sleep:
            U.run_job('c'*32)
            self.assertEqual(execute.call_count,2)
            sleep.assert_called_once_with(2)
            self.assertEqual(self.records['job.json']['phase'],'complete')

    def test_waiter_never_reports_a_different_attempt_as_recovery_success(self):
        with patch.object(U,'inspection',return_value={'current':'e'*64,'phases':['rolled-back.json']}):
            with self.assertRaises(ValueError):U.wait_original_phase('d'*64,'rolled-back.json')

    def test_stale_offer_arbitrary_url_extra_fields_or_wrong_rollback_never_dispatch(self):
        self.harness()
        cases = [{'action':'install','token':'a'*32,'requestId':'c'*32}, {'action':'check','url':'https://evil.invalid'},
                 {'action':'rollback','token':'f'*64,'requestId':'c'*32}, {'action':'install','token':'b'*32,'requestId':'../../etc'}]
        with patch.object(U, 'command') as execute:
            for payload in cases:
                with self.assertRaises(ValueError): U.dispatch(payload)
            execute.assert_not_called()

    def test_wrong_download_digest_refuses_before_package_parsing_or_execution(self):
        with patch.object(U, 'protected', return_value=b'foreign launcher'), patch.object(U.E, 'package_entries') as parse:
            with self.assertRaises(ValueError): U.authenticate_file(Path('/fixed.pyz'), {'size': 16, 'sha256':'a'*64})
            parse.assert_not_called()

    def test_untrusted_launcher_never_executes_even_if_it_claims_verify_success(self):
        raw = b'payload'
        with patch.object(U, 'protected', return_value=raw), patch.object(U.E, 'package_entries', return_value={}) as parse, \
             patch.object(U.E, 'authenticate', side_effect=ValueError('Invalid Ed25519 signature')):
            with self.assertRaises(ValueError): U.authenticate_file(Path('/fixed.pyz'), {'size':len(raw),'sha256':U.hashlib.sha256(raw).hexdigest()})
            parse.assert_called_once()


if __name__ == '__main__': unittest.main()
