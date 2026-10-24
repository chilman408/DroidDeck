# SPDX-License-Identifier: GPL-3.0-only
import configparser
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('mesa_allocator_test', Path(__file__).with_name('waydroid-gpu-mesa-allocator-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
BASE = b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=4\npersist.waydroid.use_subsurface=false\nunrelated=https://example/%20\n'

class AllocatorTests(unittest.TestCase):
    def test_only_distinct_allocator_is_added_to_layer_candidate(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(tool.candidate(BASE).decode())
        self.assertEqual(parser['properties']['ro.hardware.gralloc'], 'minigbm_gbm_mesa')
        self.assertEqual(parser['properties']['persist.waydroid.use_subsurface'], 'true')
        self.assertEqual(parser['properties']['debug.stagefright.ccodec'], '4')
        self.assertEqual(parser['properties']['ro.waydroid.software_rendering'], '0')
        del parser['properties']['ro.hardware.gralloc']
        before = configparser.ConfigParser(interpolation=None)
        before.read_string(tool.baseline_candidate(BASE).decode())
        self.assertEqual({s: dict(parser[s]) for s in parser}, {s: dict(before[s]) for s in before})

    def test_refuses_foreign_overrides_and_wrong_baseline(self):
        for data in (BASE + b'ro.hardware.gralloc=minigbm\n', BASE + b'ro.hardware.egl=mesa\n',
                     BASE.replace(b'ccodec=4', b'ccodec=3')):
            with self.assertRaises(ValueError):
                tool.candidate(data)

    def test_separate_identity_and_exact_recovery(self):
        self.assertEqual(tool.engine.BACKUP.name, 'gpu-render-mesa-allocator-20261011o')
        self.assertEqual(tool.engine.ORIGINAL.parent, tool.engine.BACKUP)
        self.assertTrue(tool.engine.LOCK.name.endswith('20261011o.lock'))
        self.assertEqual(tool.engine.RESTORED_PROPERTIES['ro.hardware.gralloc'], 'default')
        self.assertEqual(tool.engine.RESTORED_PROPERTIES['persist.waydroid.use_subsurface'], 'false')
        self.assertEqual(tool.engine.EXPECTED_NATIVE[tool.SERVICE], 'running')
        self.assertNotIn(b'gralloc', tool.engine.INIT_CODE)
        self.assertNotIn(b'ctl.', tool.engine.INIT_CODE)

    def test_consumer_must_load_both_mapper_and_allocator_libraries(self):
        base = '100-200 r-xp 0 00:00 1 '
        lib = base + '/vendor/lib64/libminigbm_gralloc_gbm_mesa.so\n'
        mapper = base + '/vendor/lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm_gbm_mesa.so\n'
        self.assertTrue(tool.mapper_loaded(lib + mapper))
        for text in (lib, mapper, lib + mapper.replace('minigbm_gbm_mesa', 'minigbm'),
                     lib + mapper.replace('.so', '.so.foreign')):
            self.assertFalse(tool.mapper_loaded(text))
        with self.assertRaises(RuntimeError):
            tool.mapper_loaded('x' * (4 * 1024 * 1024 + 1))

if __name__ == '__main__':
    unittest.main()
