# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('sign_inputs_tested', Path(__file__).with_name('release-sign-inputs.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)


class Policy(unittest.TestCase):
    def setUp(self):
        from update_admission_test import fixture, statement
        self.bundle, self.manifest, _proof = fixture()
        key = T.A.SPKI + bytes(range(32))
        self.pin = T.P.sha(key)
        self.statement = statement(self.manifest, self.bundle, key)

    def test_exact_reviewed_release_inputs_without_key_or_payload_execution(self):
        result = T.review(self.bundle, self.statement, self.pin)
        self.assertEqual(result['statementSha256'], T.P.sha(self.statement))
        self.assertEqual(result['bundleSha256'], T.P.sha(self.bundle))
        self.assertFalse(result['payloadExecuted']); self.assertFalse(result['releaseSigned'])

    def test_wrong_domain_key_bound_metadata_and_archive_refuse(self):
        value = T.P.document(self.statement)
        for changes in ({'schema': True}, {'kind': 'another-product'}, {'channel': 'stable'},
                        {'keySha256': 'f' * 64}, {'dataPolicy': 'erase'}, {'extra': 'refused'},
                        {'manifestSha256': 'f' * 64}, {'target': {}}, {'base': {}}, {'platform': {}},
                        {'bundleSha256': 'f' * 64}):
            with self.assertRaises(ValueError): T.review(self.bundle, T.P.encode({**value, **changes}), self.pin)
        with self.assertRaises(ValueError): T.review(self.bundle + b'x', self.statement, self.pin)
        with self.assertRaises(ValueError): T.review(self.bundle, self.statement, 'f' * 64)

    def test_noncanonical_duplicate_and_oversized_statement_refuse(self):
        for raw in (b'', self.statement[:-1], self.statement + b' ', b'x' * 8193,
                    self.statement.replace(b'"schema":1', b'"schema":1,"schema":1')):
            with self.assertRaises(ValueError): T.review(self.bundle, raw, self.pin)


if __name__ == '__main__':
    unittest.main()
