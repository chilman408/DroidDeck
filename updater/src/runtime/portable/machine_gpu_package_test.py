# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
FILE = HERE / 'machine-gpu-package.py'


def module():
    if not FILE.exists():
        return None
    spec = importlib.util.spec_from_file_location('held_gpu_package', FILE)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


P = module()


class PackageBoundary(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'Independent held source package verifier required')

    def test_independent_eleven_pins_include_initial_launcher(self):
        self.assertEqual(len(P.PINS), 11)
        self.assertEqual(P.PINS['machine-gpu-entry.py'],
            (4890, '534bcdd629dca1c48f92663be42d5708d313ba62f7b37dc9155ace18db50ca70'))
        for name, pin in P.PINS.items():
            source = HERE / name if '/' not in name else HERE.parents[1] / name.removeprefix('machine-gpu-runtime/')
            raw = source.read_bytes().replace(b'\r\n', b'\n')
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), pin)
        with self.assertRaises(TypeError):
            P.PINS['foreign'] = (1, '0' * 64)

    def test_no_install_or_start_dispatch_before_reads(self):
        with mock.patch.object(P, 'hold_source_package', side_effect=AssertionError('no source reads')):
            for args in ([], ['--install'], ['--watch'], ['--work'], ['--force'], ['--verify-package'],
                         ['--verify-package', '/root/generated', '--activate']):
                with self.assertRaises(ValueError):
                    P.main(args)

    def test_root_linux_and_canonical_paths_before_reads(self):
        with ExitStack() as stack, mock.patch.object(P.os, 'open', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_source_package(stack, '/root/generated')
            with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
                for root in ('relative', '/', '//root/source', '/root//source', '/root/source/', '/root/../source', '/root/\x00source'):
                    with self.assertRaises(ValueError):
                        P.hold_source_package(stack, root)

    def test_publication_boundary_refuses_before_source_reads_or_writes(self):
        self.assertTrue(callable(getattr(P, 'publish_source_package', None)), 'New-directory source publication required')
        with ExitStack() as stack, mock.patch.object(P, 'hold_source_package', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.publish_source_package(stack, '/root/source', '/root/parent', 'published')
            with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
                for name in ('', '.', '..', '../target', 'a/b', 'UPPER', 'a' * 65):
                    with self.assertRaises(ValueError):
                        P.publish_source_package(stack, '/root/source', '/root/parent', name)
                with self.assertRaises(ValueError):
                    P.publish_source_package(stack, '/root/source', '/root//parent', 'published')

    def test_source_owner_policy_is_explicit_closed_and_checked_before_reads(self):
        self.assertIn('owner', inspect.signature(P.hold_source_package).parameters)
        self.assertIn('source_owner', inspect.signature(P.publish_source_package).parameters)
        with ExitStack() as stack, mock.patch.object(P.sys, 'platform', 'linux'), \
                mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P.os, 'open', side_effect=AssertionError('no source reads')):
            for owner in (None, [1000, 1000], (1000,), (True, 1000), (1000, False),
                          (-1, 1000), (1000, -1), (0, 1000), (1000, 0), (2**31, 1000)):
                with self.assertRaises(ValueError):
                    P.hold_source_package(stack, '/root/source', owner=owner)
                with self.assertRaises(ValueError):
                    P.publish_source_package(stack, '/root/source', '/root/parent', 'published', source_owner=owner)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux generated package fixtures required')
class HeldPackage(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'Independent held source package verifier required')
        self.temp = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-held-package-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'payload'
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Native Node source package builder required')
        subprocess.run([node, '-e', 'require(process.argv[1]).buildMachineGpuBundle({output:process.argv[2]})',
            str(HERE / 'machine-gpu-bundle.js'), str(self.package)], check=True, capture_output=True, timeout=15)

    def hold(self, stack):
        return P.hold_source_package(stack, self.package)

    def snapshot(self):
        return {str(p.relative_to(self.package)): (p.lstat(), p.read_bytes() if p.is_file() else None)
                for p in [self.package, *self.package.rglob('*')]}

    def test_actual_node_bundle_held_without_execution_or_mutation(self):
        before = self.snapshot()
        with ExitStack() as stack, mock.patch('builtins.exec', side_effect=AssertionError('no payload execution')):
            ctx = self.hold(stack)
            ctx['check']()
            for name, pin in P.PINS.items():
                raw = ctx['read'](name)
                self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), pin)
            self.assertEqual(len(ctx['files']), 12)
            self.assertEqual(ctx['policy']['payloadSourcesVerified'], 11)
            self.assertTrue(ctx['policy']['entryRecipeVerified'])
            for key in P.FALSE_FLAGS:
                self.assertFalse(ctx['policy'][key])
            self.assertNotIn(str(self.root), json.dumps(ctx['policy']))
            with self.assertRaises(ValueError):
                ctx['read']('../foreign')
        self.assertEqual(self.snapshot(), before)

    def test_real_readonly_cli_and_refusing_install(self):
        result = subprocess.run(['/usr/bin/python3', '-I', '-B', str(FILE), '--verify-package', str(self.package)],
            capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(json.loads(result.stdout)['payloadSourcesVerified'], 11)
        self.assertNotIn(str(self.root), result.stdout)
        refused = subprocess.run(['/usr/bin/python3', '-I', '-B', str(FILE), '--install', str(self.package)],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(refused.stdout, '')
        self.assertNotIn(str(self.root), refused.stderr)

    def test_forged_manifest_cannot_adopt_foreign_entry(self):
        entry = self.package / 'machine-gpu-entry.py'
        entry.write_bytes(b'raise AssertionError("foreign launcher executed")\n')
        marker = self.package / 'bundle-manifest.json'
        manifest = json.loads(marker.read_bytes())
        record = next(r for r in manifest['files'] if r['path'] == entry.name)
        record.update(size=entry.stat().st_size, sha256=hashlib.sha256(entry.read_bytes()).hexdigest())
        marker.write_text(json.dumps(manifest, indent=2) + '\n')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_same_size_corruption_and_manifest_readiness_refuse(self):
        entry = self.package / 'machine-gpu-entry.py'
        raw = entry.read_bytes()
        entry.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        entry.write_bytes(raw)
        marker = self.package / 'bundle-manifest.json'
        manifest = json.loads(marker.read_bytes())
        manifest['readyForActivation'] = True
        marker.write_text(json.dumps(manifest, indent=2) + '\n')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_original_generation_retained_for_payload_and_manifest(self):
        for name in ('machine-gpu-entry.py', 'bundle-manifest.json',
                     'machine-gpu-runtime/runtime/portable/machine-gpu-lifecycle-state.py'):
            with ExitStack() as stack:
                ctx = self.hold(stack)
                file = self.package / name
                replacement = self.root / 'replacement'
                replacement.write_bytes(file.read_bytes())
                replacement.chmod(0o600)
                os.replace(replacement, file)
                with self.assertRaises(ValueError):
                    ctx['check']()
                with self.assertRaises(ValueError):
                    ctx['read']('machine-gpu-bootstrap.py')

    def test_original_ancestor_generation_retained(self):
        for relative in ('machine-gpu-runtime/runtime', ''):
            with ExitStack() as stack:
                ctx = self.hold(stack)
                original = self.package / relative
                displaced = self.root / ('displaced-' + ('inner' if relative else 'root'))
                original.rename(displaced)
                shutil.copytree(displaced, original)
                with self.assertRaises(ValueError):
                    ctx['check']()
                with self.assertRaises(ValueError):
                    ctx['read']('machine-gpu-entry.py')

    def test_permissions_ownership_links_aliases_are_not_repaired(self):
        entry = self.package / 'machine-gpu-entry.py'
        for mode in (0o644, 0o4600):
            entry.chmod(mode)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            self.assertEqual(entry.stat().st_mode & 0o7777, mode)
        entry.chmod(0o600)
        os.chown(entry, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.chown(entry, 0, 0)
        alias = self.root / 'alias'
        os.link(entry, alias)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        alias.unlink()
        alias.symlink_to(self.package, target_is_directory=True)
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            P.hold_source_package(stack, alias)
        self.package.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_unknown_files_and_closed_lifetime_refuse(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
        with self.assertRaises((OSError, ValueError)):
            ctx['check']()
        foreign = self.package / 'private-looking-generated'
        foreign.write_text('generated fixture only')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.assertEqual(foreign.read_text(), 'generated fixture only')

    def test_changed_source_during_read_never_returns_bytes(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            original = P._bytes
            changed = False
            def raced(fd, size):
                nonlocal changed
                raw = original(fd, size)
                if not changed:
                    changed = True
                    file = self.package / 'machine-gpu-entry.py'
                    file.write_bytes(file.read_bytes() + b' ')
                return raw
            with mock.patch.object(P, '_bytes', side_effect=raced), self.assertRaises(ValueError):
                ctx['read']('machine-gpu-entry.py')

    def publication_parent(self):
        self.assertTrue(callable(getattr(P, 'publish_source_package', None)), 'New-directory source publication required')
        parent = self.root / 'publication'
        parent.mkdir(mode=0o700)
        return parent

    def test_new_publication_matches_source_and_retains_original_output(self):
        parent = self.publication_parent()
        before = self.snapshot()
        with ExitStack() as stack:
            ctx = P.publish_source_package(stack, self.package, parent, 'published')
            self.assertTrue(ctx['policy']['newSourcePackagePublished'])
            self.assertTrue(ctx['policy']['sourcePackageVerified'])
            for key in P.FALSE_FLAGS:
                self.assertFalse(ctx['policy'][key])
            self.assertEqual({p.name for p in parent.iterdir()}, {'published'})
            for name in ctx['files']:
                self.assertEqual(ctx['read'](name), (self.package / name).read_bytes())
            target = parent / 'published/machine-gpu-entry.py'
            replacement = self.root / 'replacement-output'
            replacement.write_bytes(target.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, target)
            with self.assertRaises(ValueError):
                ctx['read']('machine-gpu-entry.py')
        self.assertEqual(before, self.snapshot())

    def test_existing_or_foreign_parent_entries_preserved(self):
        parent = self.publication_parent()
        sentinel = parent / 'published'
        sentinel.write_text('generated foreign sentinel')
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual(sentinel.read_text(), 'generated foreign sentinel')
        sentinel.rename(parent / 'foreign')
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual({p.name for p in parent.iterdir()}, {'foreign'})

    def test_bad_input_or_space_leaves_destination_empty(self):
        parent = self.publication_parent()
        target = self.package / 'machine-gpu-entry.py'
        raw = target.read_bytes()
        target.write_bytes(raw + b' ')
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual(list(parent.iterdir()), [])
        target.write_bytes(raw)
        with ExitStack() as stack, mock.patch.object(P.os, 'fstatvfs', return_value=mock.Mock(f_bavail=0, f_frsize=4096)), self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual(list(parent.iterdir()), [])

    def test_partial_write_is_retained_and_retry_refused(self):
        parent = self.publication_parent()
        with ExitStack() as stack, mock.patch.object(P.os, 'fsync', side_effect=OSError('generated flush failure')):
            with self.assertRaises(P.SourcePublicationUncertain):
                P.publish_source_package(stack, self.package, parent, 'published')
        attempts = list(parent.iterdir())
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0].name.startswith('.source-attempt-'))
        self.assertFalse((attempts[0] / P.MANIFEST).exists())
        self.assertFalse((parent / 'published').exists())
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.hold_source_package(stack, attempts[0])
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual(list(parent.iterdir()), attempts)

    def test_source_drift_after_copy_cannot_publish(self):
        parent = self.publication_parent()
        original = P._write_private
        def raced(*args):
            result = original(*args)
            entry = self.package / 'machine-gpu-entry.py'
            entry.write_bytes(entry.read_bytes() + b' ')
            return result
        with ExitStack() as stack, mock.patch.object(P, '_write_private', side_effect=raced), self.assertRaises(P.SourcePublicationUncertain):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertFalse((parent / 'published').exists())
        self.assertFalse(any(parent.rglob(P.MANIFEST)))

    def test_same_bytes_output_replacement_cannot_publish(self):
        parent = self.publication_parent()
        original = P._write_private
        def raced(stack, directory, name, raw):
            result = original(stack, directory, name, raw)
            directory_path = Path(os.readlink('/proc/self/fd/' + str(directory)))
            replacement = self.root / 'replacement-write'
            replacement.write_bytes(raw)
            replacement.chmod(0o600)
            os.replace(replacement, directory_path / name)
            return result
        with ExitStack() as stack, mock.patch.object(P, '_write_private', side_effect=raced), self.assertRaises(P.SourcePublicationUncertain):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertFalse((parent / 'published').exists())

    def test_concurrent_target_at_publication_is_not_replaced(self):
        parent = self.publication_parent()
        original = P._rename_new
        def raced(directory, old, new):
            target = parent / new
            target.mkdir(mode=0o700)
            (target / 'sentinel').write_text('generated concurrent target')
            return original(directory, old, new)
        with ExitStack() as stack, mock.patch.object(P, '_rename_new', side_effect=raced), self.assertRaises(P.SourcePublicationUncertain):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual((parent / 'published/sentinel').read_text(), 'generated concurrent target')
        self.assertEqual(len(list(parent.iterdir())), 2)

    def test_parent_flush_failure_after_rename_reports_uncertainty(self):
        parent = self.publication_parent()
        original = P.os.fsync
        def failed(fd):
            if (parent / 'published').exists():
                raise OSError('generated postpublication flush failure')
            return original(fd)
        with ExitStack() as stack, mock.patch.object(P.os, 'fsync', side_effect=failed), self.assertRaises(P.SourcePublicationUncertain):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertTrue((parent / 'published' / P.MANIFEST).exists())
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')

    def test_attempt_creation_failure_after_mkdir_is_uncertain(self):
        parent = self.publication_parent()
        original = P.os.mkdir
        def failed(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if str(name).startswith('.source-attempt-'):
                raise OSError('generated ambiguous attempt creation')
            return result
        with ExitStack() as stack, mock.patch.object(P.os, 'mkdir', side_effect=failed), self.assertRaises(P.SourcePublicationUncertain):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual(len(list(parent.iterdir())), 1)
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')

    def test_output_ancestor_substitution_stops_before_payload_writes(self):
        parent = self.publication_parent()
        original = P.os.mkdir
        def raced(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == 'machine-gpu-runtime':
                attempt = next(parent.iterdir())
                displaced = self.root / 'displaced-publication'
                attempt.rename(displaced)
                shutil.copytree(displaced, attempt)
            return result
        with ExitStack() as stack, mock.patch.object(P.os, 'mkdir', side_effect=raced), self.assertRaises(P.SourcePublicationUncertain):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertFalse((parent / 'published').exists())
        self.assertFalse(any(p.is_file() for p in parent.rglob('*')))

    def test_alias_or_nonprivate_publication_parent_refuses(self):
        parent = self.publication_parent()
        alias = self.root / 'publication-alias'
        alias.symlink_to(parent, target_is_directory=True)
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            P.publish_source_package(stack, self.package, alias, 'published')
        parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published')
        self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual(parent.stat().st_mode & 0o777, 0o755)

    def user_owned_source(self):
        for path in [self.package, *self.package.rglob('*')]:
            os.chown(path, 1000, 1000)
        return (1000, 1000)

    def test_known_user_source_requires_explicit_matching_owner(self):
        owner = self.user_owned_source()
        before = self.snapshot()
        with ExitStack() as stack:
            ctx = P.hold_source_package(stack, self.package, owner=owner)
            self.assertTrue(ctx['policy']['sourceOwnerMatched'])
            self.assertFalse(ctx['policy']['rootPrivateSourcePackage'])
            self.assertEqual(len(ctx['read']('machine-gpu-entry.py')), 4890)
        for expected in ((0, 0), (1001, 1000), (1000, 1001)):
            with ExitStack() as stack, self.assertRaises(ValueError):
                P.hold_source_package(stack, self.package, owner=expected)
        self.assertEqual(self.snapshot(), before)

    def test_user_source_publishes_root_copy_without_chowning_input(self):
        owner = self.user_owned_source()
        before = self.snapshot()
        parent = self.publication_parent()
        with ExitStack() as stack:
            ctx = P.publish_source_package(stack, self.package, parent, 'published', source_owner=owner)
            self.assertTrue(ctx['policy']['rootPrivateSourcePackage'])
            for name in ctx['files']:
                target = parent / 'published' / name
                info = target.lstat()
                self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o7777, info.st_nlink), (0, 0, 0o600, 1))
                self.assertEqual(ctx['read'](name), (self.package / name).read_bytes())
            for key in P.FALSE_FLAGS:
                self.assertFalse(ctx['policy'][key])
        self.assertEqual(before, self.snapshot())

    def test_user_ancestor_owner_drift_invalidates_context(self):
        owner = self.user_owned_source()
        ancestor = self.root / 'user-parent'
        ancestor.mkdir(mode=0o700)
        os.chown(ancestor, *owner)
        self.package.rename(ancestor / 'payload')
        self.package = ancestor / 'payload'
        for changed in ((1001, 1001), (0, 0)):
            with ExitStack() as stack:
                ctx = P.hold_source_package(stack, self.package, owner=owner)
                os.chown(ancestor, *changed)
                with self.assertRaises(ValueError):
                    ctx['read']('machine-gpu-entry.py')
                os.chown(ancestor, *owner)

    def test_user_ownership_drift_during_copy_preserves_unready_attempt(self):
        owner = self.user_owned_source()
        parent = self.publication_parent()
        original = P._write_private
        def raced(*args):
            result = original(*args)
            os.chown(self.package / 'machine-gpu-entry.py', 1001, 1001)
            return result
        with ExitStack() as stack, mock.patch.object(P, '_write_private', side_effect=raced), self.assertRaises(P.SourcePublicationUncertain):
            P.publish_source_package(stack, self.package, parent, 'published', source_owner=owner)
        self.assertFalse((parent / 'published').exists())
        self.assertEqual(len(list(parent.iterdir())), 1)
        self.assertFalse(any(parent.rglob(P.MANIFEST)))

    def test_user_source_cannot_weaken_destination_ownership(self):
        owner = self.user_owned_source()
        parent = self.publication_parent()
        os.chown(parent, *owner)
        with ExitStack() as stack, self.assertRaises(ValueError):
            P.publish_source_package(stack, self.package, parent, 'published', source_owner=owner)
        self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual((parent.stat().st_uid, parent.stat().st_gid), owner)

    def test_published_root_copy_is_independent_of_later_user_changes(self):
        owner = self.user_owned_source()
        parent = self.publication_parent()
        with ExitStack() as stack:
            ctx = P.publish_source_package(stack, self.package, parent, 'published', source_owner=owner)
            entry = self.package / 'machine-gpu-entry.py'
            entry.write_bytes(b'generated changed user source')
            os.chown(entry, 1001, 1001)
            ctx['check']()
            self.assertEqual(hashlib.sha256(ctx['read'](entry.name)).hexdigest(), P.PINS[entry.name][1])


if __name__ == '__main__':
    unittest.main()
