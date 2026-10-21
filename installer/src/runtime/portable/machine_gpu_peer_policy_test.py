# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S = load('machine_peer_policy', 'machine-gpu-peer-policy.py')
E = load('peer_projection_fixture', 'effective-units.py')


def expected(projector=E):
    return {name: S.project(projector, *S.properties(name)) for name in S.UNITS}


class PeerPolicy(unittest.TestCase):
    def test_independent_closed_unit_recipes_have_no_healthy_expiry(self):
        for name in S.UNITS:
            raw = S.unit_bytes(name)
            self.assertIn(b'RuntimeMaxSec=infinity\n', raw)
            self.assertIn(b'KillMode=control-group\nKillSignal=SIGKILL\n', raw)
            self.assertIn(b'Restart=no\n', raw)
            self.assertIn(b'/usr/bin/python3 -I -B ', raw)
            self.assertNotIn(b'ExecStopPost=', raw)
            self.assertNotIn(b'[Install]', raw)
            service, _unit = S.properties(name)
            service['Environment'].append('generated-private-sentinel=value')
            self.assertNotIn('generated-private-sentinel=value', S.properties(name)[0]['Environment'])
        self.assertIn(('BindsTo=' + S.WATCHER).encode(), S.unit_bytes(S.WORKER))
        self.assertNotIn(S.WORKER.encode(), S.unit_bytes(S.WATCHER))
        for name in ('droiddeck-gpu-recovery.service', '../foreign', True):
            with self.assertRaises(ValueError):
                S.unit_bytes(name)

    def test_every_projected_field_extra_missing_and_bool_int_drift_refuse(self):
        records = expected()
        S.require_policy(E, records)
        for name, record in records.items():
            for key, value in record.items():
                changed = copy.deepcopy(records)
                changed[name][key] = (not value if type(value) is bool else value + 1 if type(value) is int else
                    value + ['generated-foreign'] if type(value) is list else value + '-foreign')
                with self.subTest(name=name, key=key), self.assertRaises(ValueError):
                    S.require_policy(E, changed)
            changed = copy.deepcopy(records)
            del changed[name]['Type']
            with self.assertRaises(ValueError):
                S.require_policy(E, changed)
        for change in ({**records, 'foreign.service': {}}, {S.WORKER: records[S.WORKER]}):
            with self.assertRaises(ValueError):
                S.require_policy(E, change)
        changed = copy.deepcopy(records)
        changed[S.WORKER]['DefaultDependencies'] = 0
        with self.assertRaises(ValueError):
            S.require_policy(E, changed)

    def test_extended_command_flags_and_external_environment_are_not_hidden(self):
        for field in ('ExecStartEx', 'ExecStopPostEx', 'Environment', 'EnvironmentFiles', 'PassEnvironment', 'UnsetEnvironment'):
            service, unit = S.properties(S.WORKER)
            if field == 'ExecStartEx':
                row = list(service[field][0])
                row[2] = ['ignore-failure', 'privileged']
                service[field] = [row]
            elif field == 'ExecStopPostEx':
                service[field] = [('/bin/true', ['/bin/true'], [], 0, 0, 0, 0, 0, 0, 0)]
            elif field == 'EnvironmentFiles':
                service[field] = [('/generated-private-sentinel.env', False)]
            else:
                service[field] = ['generated-private-sentinel=value']
            records = expected()
            records[S.WORKER] = S.project(E, service, unit)
            with self.assertRaises(ValueError):
                S.require_policy(E, records)
        self.assertNotIn('generated-private', json.dumps(expected()))

    def fake_bus(self, *, change=False, fail=False):
        names, closed = [], []
        counts = {}
        class Props:
            def __init__(self, name):
                self.name = name
            def GetAll(self, interface, timeout):
                if timeout != 5:
                    raise AssertionError('Unbounded read')
                service, unit = S.properties(self.name)
                if interface.endswith('.Service'):
                    counts[self.name] = counts.get(self.name, 0) + 1
                    if change and counts[self.name] > 1:
                        service['KillMode'] = 'process'
                    return service
                return unit
        class Manager:
            def GetUnit(self, name, timeout):
                names.append(name)
                if fail:
                    raise ValueError('generated missing unit')
                if timeout != 5:
                    raise AssertionError('Unbounded read')
                return name
            def LoadUnit(self, *_args, **_kwargs):
                raise AssertionError('Must never load prospective units')
        manager = Manager()
        class Bus:
            def get_object(self, destination, path):
                if destination != 'org.freedesktop.systemd1':
                    raise AssertionError('Foreign bus destination')
                return manager if path == '/org/freedesktop/systemd1' else Props(path)
            def close(self):
                closed.append(True)
        def system_bus(**kwargs):
            self.assertEqual(kwargs, {'private': True})
            return Bus()
        return SimpleNamespace(SystemBus=system_bus, Interface=lambda obj, _interface: obj), names, closed

    def test_snapshot_queries_only_new_loaded_units_and_closes_bus(self):
        dbus, names, closed = self.fake_bus()
        with mock.patch.dict(S.os.environ, {}, clear=True), mock.patch.dict('sys.modules', {'dbus': dbus}):
            self.assertEqual(S.snapshot(E), expected())
        self.assertEqual(names, list(S.UNITS))
        self.assertEqual(closed, [True])

    def test_service_change_between_snapshots_or_missing_unit_closes_and_refuses(self):
        for kwargs in ({'change': True}, {'fail': True}):
            dbus, _, closed = self.fake_bus(**kwargs)
            with mock.patch.dict(S.os.environ, {}, clear=True), mock.patch.dict('sys.modules', {'dbus': dbus}), self.assertRaises(ValueError):
                S.snapshot(E)
            self.assertEqual(closed, [True])

    def test_environment_bus_redirect_refuses_before_connection(self):
        with mock.patch.dict(S.os.environ, {'DBUS_SYSTEM_BUS_ADDRESS': 'generated-untrusted'}), self.assertRaises(ValueError):
            S.snapshot(E)

    def test_root_linux_precedes_source_or_unit_reads(self):
        source = {'check': mock.Mock(side_effect=AssertionError('must not inspect'))}
        with ExitStack() as stack:
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(S.sys, 'platform', platform), mock.patch.object(S.os, 'geteuid', return_value=uid, create=True), self.assertRaises(ValueError):
                    S.hold_peer_recipe(mock.Mock(), stack, source)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated service/source fixtures required')
class HeldPeerRecipes(unittest.TestCase):
    def setUp(self):
        self.T = load('peer_source_fixtures', 'machine_gpu_sources_test.py')
        self.f = self.T.ProtectedSources()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.copier = self.f.copier
        self.root = self.f.root.parent / 'units'
        self.root.mkdir(mode=0o700)
        for name in S.UNITS:
            file = self.root / name
            file.write_bytes(S.unit_bytes(name))
            file.chmod(0o644)
        self.records = expected()

    def hold(self, stack):
        sources = self.f.hold(stack)
        return S.hold_peer_recipe(self.copier, stack, sources, self.root, read_effective=lambda _projector: copy.deepcopy(self.records))

    def test_exact_files_and_generated_effective_policy_are_readonly_not_full_readiness(self):
        before = {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.root.iterdir()}
        with ExitStack() as stack:
            held = self.hold(stack)
            held['check']()
            self.assertTrue(held['peerRecipeFilesMatched'])
            self.assertTrue(held['effectivePeerPolicyMatched'])
            for key in ('installedUnitRootMatched', 'effectiveObservationVerified', 'installedRecipeVerified', 'effectiveGraphVerified',
                        'independentRecoveryArmed', 'servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation'):
                self.assertFalse(held[key])
            self.assertNotIn(str(self.root), json.dumps({k: v for k, v in held.items() if k != 'check'}))
        self.assertEqual({p.name: (p.stat().st_ino, p.read_bytes()) for p in self.root.iterdir()}, before)

    def test_foreign_unit_bytes_modes_links_and_dropin_namespace_are_preserved(self):
        file = self.root / S.WORKER
        raw = file.read_bytes()
        for changed in (raw + b'# generated\n', raw.replace(b'KillMode=control-group', b'KillMode=process')):
            file.write_bytes(changed)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            self.assertEqual(file.read_bytes(), changed)
        file.write_bytes(raw)
        file.chmod(0o600)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        file.chmod(0o644)
        link = self.root.parent / 'linked'
        os.link(file, link)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        link.unlink()
        dropin = self.root / (S.WORKER + '.d')
        dropin.symlink_to(self.root.parent / 'missing-directory', target_is_directory=True)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.assertTrue(dropin.is_symlink())

    def test_same_bytes_replacement_or_effective_drift_invalidates_held_admission(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            replacement = self.root / 'replacement'
            file = self.root / S.WORKER
            replacement.write_bytes(file.read_bytes())
            replacement.chmod(0o644)
            os.replace(replacement, file)
            with self.assertRaises(ValueError):
                held['check']()
        with ExitStack() as stack:
            held = self.hold(stack)
            self.records[S.WATCHER]['Restart'] = 'always'
            with self.assertRaises(ValueError):
                held['check']()

    def test_unit_mutation_during_effective_observation_refuses(self):
        with ExitStack() as stack:
            sources = self.f.hold(stack)
            def changed(_projector):
                file = self.root / S.WATCHER
                file.write_bytes(file.read_bytes() + b'# generated race\n')
                return expected()
            with self.assertRaises(ValueError):
                S.hold_peer_recipe(self.copier, stack, sources, self.root, read_effective=changed)

    def test_source_drift_invalidates_peer_recipe_without_unit_repair(self):
        with ExitStack() as stack:
            held = self.hold(stack)
            file = self.f.root / self.T.S.ROLES['health']
            file.write_bytes(file.read_bytes() + b' ')
            with self.assertRaises(ValueError):
                held['check']()

    def test_unrelated_unit_is_not_modified_or_disallowed(self):
        foreign = self.root / 'unrelated.service'
        foreign.write_bytes(b'generated foreign sentinel')
        with ExitStack() as stack:
            self.hold(stack)['check']()
        self.assertEqual(foreign.read_bytes(), b'generated foreign sentinel')

    def test_policy_worker_composes_sources_recipes_and_real_pidfds(self):
        _, _, kwargs = self.f.peers.peers()
        kwargs.pop('admit')
        with ExitStack() as stack:
            sources = self.f.hold(stack)
            held = S.hold_policy_worker(self.copier, stack, sources, self.root,
                read_effective=lambda _projector: copy.deepcopy(self.records), **kwargs)
            held['check']()
            self.assertTrue(held['componentSourcesVerified'])
            self.assertTrue(held['peerLifetimeGuardHeld'])
            self.assertFalse(held['independentRecoveryArmed'])
            self.records[S.WORKER]['TimeoutStopFailureMode'] = 'terminate'
            with self.assertRaises(ValueError):
                held['check']()

    def test_effective_drift_after_owned_action_is_uncertain_without_next_action(self):
        recovery, p = self.f.peers.recovery()
        _, _, kwargs = self.f.peers.peers()
        kwargs.pop('admit')
        rename = p.rename_exclusive
        actions = []
        def changed(*args, **kw):
            rename(*args, **kw)
            actions.append(args[1:])
            self.records[S.WORKER]['KillMode'] = 'process'
        with ExitStack() as stack:
            sources = self.f.hold(stack)
            held = S.hold_policy_worker(self.copier, stack, sources, self.root,
                read_effective=lambda _projector: copy.deepcopy(self.records), **kwargs)
            with mock.patch.object(p, 'rename_exclusive', side_effect=changed), self.assertRaises(p.PromotionUncertain):
                recovery.run_recovery(supervised=held['check'])
        self.assertEqual(len(actions), 1)
        self.assertEqual(json.loads(recovery.journal.record.read_bytes())['sequence'], 0)
        self.assertTrue(recovery.status_fixture.status()['checkpointRequired'])


if __name__ == '__main__':
    unittest.main()
