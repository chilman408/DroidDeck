# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('publication_storage', Path(__file__).with_name('runtime-split-role-publication.py'))
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)


class Storage(unittest.TestCase):
    def setUp(self):
        self.descriptors = {'runtime': 10, 'config': 11, 'journal': 12}
        self.plan = {role: {'sizes': (1, 4097), 'directories': 2, 'links': 1} for role in X.ROLES}
        self.infos = {fd: SimpleNamespace(st_dev=2 if fd == 11 else 1, st_ino=fd,
            st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=0) for fd in self.descriptors.values()}
        self.spaces = {fd: SimpleNamespace(f_frsize=4096, f_blocks=10000, f_bavail=10000,
            f_files=10000, f_favail=10000, f_flag=0) for fd in self.descriptors.values()}
        for patch in (mock.patch.object(X.os, 'fstat', side_effect=self.infos.__getitem__),
                      mock.patch.object(X.os, 'fstatvfs', side_effect=self.spaces.__getitem__, create=True),
                      mock.patch.object(X.os, 'ST_RDONLY', 1, create=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def check(self):
        return X._storage_preflight(self.descriptors, self.plan)

    def test_complete_two_filesystem_plan_has_no_reservation_authority(self):
        result = self.check()
        self.assertTrue(result['privateParentSpacePreflightVerified'])
        self.assertTrue(result['privatePayloadSpacePlanSupplied'])
        self.assertEqual(result['storageFilesystemsObserved'], 2)
        self.assertFalse(result['storageCapacityReserved'])
        self.assertFalse(result['storageSuccessGuaranteed'])

    def test_default_checks_metadata_only_and_does_not_claim_payload_plan(self):
        self.assertFalse(X._storage_preflight(self.descriptors)['privatePayloadSpacePlanSupplied'])

    def test_protected_live_parents_need_not_be_private_but_must_remain_root_owned(self):
        self.infos[10].st_mode = stat.S_IFDIR | 0o755
        with self.assertRaises(ValueError):
            self.check()
        self.assertTrue(X._storage_preflight(self.descriptors, self.plan, private_parents=False)['privateParentSpacePreflightVerified'])
        self.infos[10].st_mode = stat.S_IFDIR | 0o777
        with self.assertRaises(ValueError):
            X._storage_preflight(self.descriptors, self.plan, private_parents=False)
        with self.assertRaises(ValueError):
            X._storage_preflight(self.descriptors, self.plan, private_parents=0)

    def test_shared_device_demands_aggregate_not_each_role_individually(self):
        # Runtime7 + journal10 blocks: individually fit reserve+12, jointly do not.
        for fd in (10, 12):
            self.spaces[fd].f_bavail = 256 + 12
        with self.assertRaisesRegex(ValueError, 'Insufficient combined'):
            self.check()

    def test_block_rounding_boundary_and_per_filesystem_reserve(self):
        # Runtime:3file blocks+allocation1+dirs2+alias1=7; journal9files+dir=10.
        for fd in (10, 12):
            self.spaces[fd].f_bavail = 256 + 17
        self.spaces[11].f_bavail = 256 + 6
        self.check()
        self.spaces[12].f_bavail -= 1
        with self.assertRaisesRegex(ValueError, 'Insufficient combined'):
            self.check()

    def test_inode_exhaustion_and_distinct_device_shortage_refuse(self):
        self.spaces[11].f_favail = 5
        with self.assertRaisesRegex(ValueError, 'Insufficient combined'):
            self.check()
        self.spaces[11].f_favail = 10000
        self.spaces[11].f_bavail = 0
        with self.assertRaisesRegex(ValueError, 'Insufficient combined'):
            self.check()

    def test_readonly_or_unreported_inode_capacity_refuses(self):
        self.spaces[11].f_flag = 1
        with self.assertRaisesRegex(ValueError, 'Writable bounded'):
            self.check()
        self.spaces[11].f_flag = 0
        self.spaces[11].f_files = 0
        with self.assertRaisesRegex(ValueError, 'Writable bounded'):
            self.check()

    def test_malformed_kernel_counts_and_fragment_mismatch_refuse(self):
        for field, value in (('f_frsize', 0), ('f_bavail', -1), ('f_favail', True),
                             ('f_bavail', 10001), ('f_frsize', 2 ** 30)):
            before = getattr(self.spaces[10], field)
            setattr(self.spaces[10], field, value)
            with self.assertRaisesRegex(ValueError, 'Writable bounded'):
                self.check()
            setattr(self.spaces[10], field, before)
        self.spaces[12].f_frsize = 8192
        with self.assertRaisesRegex(ValueError, 'fragment'):
            self.check()

    def test_closed_bounded_counts_reject_booleans_and_excess(self):
        original = copy.deepcopy(self.plan)
        for key, value in (('sizes', (True,)), ('sizes', [1]), ('sizes', (0,)),
                           ('sizes', (8 * 1024 * 1024 + 1,)), ('directories', 0),
                           ('directories', True), ('links', 65), ('extra', 1)):
            self.plan = copy.deepcopy(original)
            self.plan['runtime'][key] = value
            with self.assertRaisesRegex(ValueError, 'Bounded role'):
                self.check()
        self.plan = original
        self.plan['runtime']['sizes'] = (8 * 1024 * 1024,) * 65
        with self.assertRaisesRegex(ValueError, 'combined storage payload'):
            self.check()

    def test_descriptor_shape_and_parent_modes_checked_without_writes(self):
        self.infos[10].st_mode = stat.S_IFDIR | 0o755
        with self.assertRaisesRegex(ValueError, 'root-owned'):
            self.check()
        self.infos[10].st_mode = stat.S_IFDIR | 0o700
        for invalid in ({}, {'runtime': 10, 'config': 11, 'journal': True}):
            with self.assertRaises(ValueError):
                X._storage_preflight(invalid, self.plan)
        with mock.patch.object(X.os, 'mkdir', side_effect=AssertionError('no writes')):
            self.check()


if __name__ == '__main__':
    unittest.main()
