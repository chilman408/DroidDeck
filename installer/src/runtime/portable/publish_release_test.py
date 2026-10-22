# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('publisher_tested', Path(__file__).with_name('publish-release.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Promotion(unittest.TestCase):
    def setUp(self):
        self.candidate = b'immutable source fixture'
        self.revision = 'a' * 40
        self.value = {'version': D.VERSION, 'sourceRevision': self.revision,
                      'candidateSha256': D.B.sha(self.candidate),
                      'freshBootId': '11111111-1111-1111-1111-111111111111',
                      'rebootBootId': '22222222-2222-2222-2222-222222222222',
                      'interruptedBootId': '33333333-3333-3333-3333-333333333333',
                      'recoveredBootId': '44444444-4444-4444-4444-444444444444', 'slowStartupSeconds': 70,
                      'phaseCount': 35, 'idleSeconds': 75, 'videoFrames': 200, 'toneHz': 1007.8125,
                      'gates': dict.fromkeys(D.GATES, True),
                      'verificationCheckpoint': 'docs/checkpoints/fixture-acceptance.md'}

    def accept(self, value):
        return D.acceptance(json.dumps(value).encode(), D.B.sha(self.candidate), self.revision)

    def test_every_gate_is_explicit_and_true(self):
        self.accept(self.value)
        for gate in D.GATES:
            for bad in (False, 1, 'true', None):
                value = {**self.value, 'gates': {**self.value['gates'], gate: bad}}
                with self.subTest(gate=gate, bad=bad), self.assertRaises(ValueError):
                    self.accept(value)
        with self.assertRaises(ValueError):
            self.accept({**self.value, 'privateKey': 'must never be copied'})

    def test_wrong_source_incomplete_metrics_or_same_boot_refuse(self):
        for name, bad in [('sourceRevision', 'b' * 40), ('candidateSha256', 'f' * 64),
                          ('phaseCount', 34), ('phaseCount', True), ('idleSeconds', 74),
                          ('videoFrames', 0), ('toneHz', float('nan')), ('toneHz', True),
                          ('freshBootId', self.value['rebootBootId']),
                          ('interruptedBootId', self.value['recoveredBootId']),
                          ('slowStartupSeconds', 60), ('slowStartupSeconds', 180), ('slowStartupSeconds', True),
                          ('verificationCheckpoint', '../../private.json')]:
            with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                self.accept({**self.value, name: bad})

    def test_duplicate_json_keys_refuse(self):
        raw = json.dumps(self.value).encode()
        with self.assertRaises(ValueError):
            D.acceptance(b'{"version":"foreign",' + raw[1:], D.B.sha(self.candidate), self.revision)

    def test_artifacts_retain_candidate_exactly_and_have_complete_checksums(self):
        license_raw = Path(D.__file__).with_name('gpl-3.0.txt').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(license_raw).hexdigest(), D.LICENSE_SHA)
        evidence = json.dumps(self.value).encode()
        first = D.payloads(self.candidate, {'sourceRevision': self.revision}, evidence, license_raw)
        second = D.payloads(self.candidate, {'sourceRevision': self.revision}, evidence, license_raw)
        self.assertEqual(first, second)
        self.assertEqual(first['droiddeck-' + D.VERSION + '-source.tar'], self.candidate)
        manifest = json.loads(first['release.json'])
        self.assertFalse(manifest['digitallySigned'])
        self.assertTrue(manifest['candidateUnchangedAfterTesting'])
        rows = dict(line.split('  ', 1)[::-1] for line in first['SHA256SUMS'].decode().splitlines())
        self.assertEqual(set(rows), set(first) - {'SHA256SUMS'})
        for name, digest in rows.items():
            self.assertEqual(D.B.sha(first[name]), digest)
        with self.assertRaises(ValueError):
            D.payloads(self.candidate, {'sourceRevision': self.revision}, evidence, b'foreign license')

    def test_failed_validation_creates_no_release_directory(self):
        with mock.patch.object(D.B, 'read_archive', return_value=b'foreign'), \
                mock.patch.object(D.B, 'read_sources', return_value={}), \
                mock.patch.object(D.B, 'read_install_sources', return_value={}), \
                mock.patch.object(D.B, 'render', return_value=(b'original', {})), \
                mock.patch.object(Path, 'mkdir') as mkdir, self.assertRaises(ValueError):
            D.main(['/repo', '/archive', '/evidence', '/new-release'])
        mkdir.assert_not_called()


if __name__ == '__main__':
    unittest.main()
