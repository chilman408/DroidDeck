# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


def load():
    source = Path(__file__).with_name('effective-units.py')
    if not source.exists():
        raise AssertionError('Read-only systemd metadata collector is missing')
    spec = importlib.util.spec_from_file_location('effective_units', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def properties():
    service = {'Type': 'simple', 'NotifyAccess': 'none', 'User': 'alice', 'Group': '1300',
               'SupplementaryGroups': ['video', 'render'], 'Environment': ['PRIVATE=generated-sentinel'],
               'EnvironmentFiles': [], 'PassEnvironment': [], 'UnsetEnvironment': [],
               'WorkingDirectory': '', 'KillMode': 'control-group', 'KillSignal': 15,
               'Restart': 'no', 'TimeoutStopUSec': 90000000, 'TimeoutStartUSec': 90000000}
    for name in ('ExecCondition', 'ExecStartPre', 'ExecStart', 'ExecStartPost', 'ExecReload', 'ExecReloadPost', 'ExecStop', 'ExecStopPost'):
        service[name + 'Ex'] = []
    service['ExecStartEx'] = [('/usr/bin/node', ['/usr/bin/node', 'generated-private-argument'], [], 0, 0, 0, 0, 0, 0, 0)]
    unit = {'LoadState': 'loaded', 'NeedDaemonReload': False, 'Transient': False,
            'FragmentPath': '/etc/systemd/system/generated.service', 'DropInPaths': [],
            'Requires': [], 'Wants': [], 'After': ['system.slice'], 'PartOf': []}
    return service, unit


class Projection(unittest.TestCase):
    def test_external_environment_sources_are_projected_without_exposing_paths(self):
        service, unit = properties()
        service['EnvironmentFiles'] = [('/generated/private-sentinel.env', False)]
        service['PassEnvironment'] = ['PRIVATE']
        service['UnsetEnvironment'] = ['DROIDDECK_CONFIGURATION_SHA256']
        record = load().project_record(service, unit)
        self.assertNotIn('private-sentinel', json.dumps(record))
        self.assertEqual(record['EnvironmentFilesSha256'], hashlib.sha256(b'[["/generated/private-sentinel.env",false]]').hexdigest())
        self.assertEqual(record['PassEnvironment'], ['PRIVATE'])
        self.assertEqual(record['UnsetEnvironment'], ['DROIDDECK_CONFIGURATION_SHA256'])

    def test_hashes_commands_flags_and_environment_without_emitting_values(self):
        service, unit = properties()
        result = load().project_record(service, unit)
        self.assertNotIn('generated-private', json.dumps(result))
        self.assertNotIn('generated-sentinel', json.dumps(result))
        self.assertEqual(result['SupplementaryGroups'], ['render', 'video'])
        self.assertEqual(result['EnvironmentSha256'], hashlib.sha256(b'["PRIVATE=generated-sentinel"]').hexdigest())
        before = result['ExecStartSha256']
        service['ExecStartEx'][0] = ('/usr/bin/node', ['/usr/bin/node', 'generated-private-argument'], ['privileged'], 0, 0, 0, 0, 0, 0, 0)
        self.assertNotEqual(load().project_record(service, unit)['ExecStartSha256'], before)

    def test_oversized_duplicate_or_wrong_type_properties_fail_closed(self):
        for key, value in (('User', 'a' * 5000), ('KillSignal', True), ('Environment', ['X=1', 'X=1']),
                           ('ExecStartEx', [('node', ['node'], True)])):
            service, unit = properties()
            service[key] = value
            with self.assertRaises((ValueError, TypeError)):
                load().project_record(service, unit)

    def test_missing_extended_commands_never_fall_back_to_flag_losing_legacy_properties(self):
        service, unit = properties()
        del service['ExecStartEx']
        service['ExecStart'] = [('/usr/bin/node', ['/usr/bin/node'], False, 0, 0, 0, 0, 0, 0, 0)]
        with self.assertRaises((ValueError, KeyError)):
            load().project_record(service, unit)

    def test_cli_overrides_refuse_before_connecting_to_system_bus(self):
        for args in ([], ['--start'], ['--snapshot', '/override'], ['--snapshot', '--force']):
            with self.assertRaises(ValueError):
                load().main(args)


if __name__ == '__main__':
    unittest.main()
