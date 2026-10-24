# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import unittest
import tempfile
from pathlib import Path
from zipfile import ZipFile

from androguard.core.axml import AXMLPrinter
from lxml import etree
from loguru import logger

from tools.taskbar_profile import ORIGINAL_SHA256, RESOURCE, disable_scalable_tablet_grid, package_overlay

logger.disable("androguard")
FIXTURE = Path(__file__).parent / "fixtures" / "trebuchet-device-profiles.axml"
NS = "{http://schemas.android.com/apk/res-auto}"


class TaskbarProfileTests(unittest.TestCase):
    def test_only_tablet_scaling_flag_changes(self):
        original = FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), ORIGINAL_SHA256)
        modified = disable_scalable_tablet_grid(original)
        self.assertEqual(len(modified), len(original))
        self.assertEqual(sum(a != b for a, b in zip(original, modified)), 4)
        expected = AXMLPrinter(original).get_xml_obj()
        tablet = expected.xpath("grid-option[@launcher:name='6_by_5']", namespaces={
            "launcher": "http://schemas.android.com/apk/res-auto"})
        self.assertEqual(len(tablet), 1)
        self.assertEqual(tablet[0].get(NS + "isScalable"), "true")
        tablet[0].set(NS + "isScalable", "false")
        actual = AXMLPrinter(modified).get_xml_obj()
        self.assertEqual(etree.tostring(actual), etree.tostring(expected))

    def test_unknown_or_already_modified_resource_is_rejected(self):
        original = FIXTURE.read_bytes()
        for value in (b"", original[:-1], original + b"x", bytes(len(original)),
                      disable_scalable_tablet_grid(original)):
            with self.subTest(size=len(value)):
                with self.assertRaisesRegex(ValueError, "Unsupported Trebuchet"):
                    disable_scalable_tablet_grid(value)

    def test_input_is_not_mutated(self):
        original = bytearray(FIXTURE.read_bytes())
        snapshot = bytes(original)
        self.assertEqual(disable_scalable_tablet_grid(original),
                         disable_scalable_tablet_grid(snapshot))
        self.assertEqual(bytes(original), snapshot)

    def test_packaging_preserves_manifest_and_resource_table(self):
        with tempfile.TemporaryDirectory() as directory:
            template, output = Path(directory) / "template.apk", Path(directory) / "overlay.apk"
            with ZipFile(template, "w") as apk:
                apk.writestr("AndroidManifest.xml", b"manifest")
                apk.writestr("resources.arsc", b"table")
                apk.writestr(RESOURCE, b"placeholder")
            package_overlay(template, FIXTURE.read_bytes(), output)
            with ZipFile(output) as apk:
                self.assertEqual(apk.read("AndroidManifest.xml"), b"manifest")
                self.assertEqual(apk.read("resources.arsc"), b"table")
                self.assertEqual(apk.read(RESOURCE), disable_scalable_tablet_grid(FIXTURE.read_bytes()))
            with self.assertRaises(FileExistsError):
                package_overlay(template, FIXTURE.read_bytes(), output)

    def test_packaging_rejects_code_or_signed_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            template, output = Path(directory) / "template.apk", Path(directory) / "overlay.apk"
            for extra in ("classes.dex", "META-INF/CERT.RSA"):
                with ZipFile(template, "w") as apk:
                    for name in ("AndroidManifest.xml", "resources.arsc", RESOURCE, extra):
                        apk.writestr(name, b"test")
                with self.assertRaisesRegex(ValueError, "resource-only"):
                    package_overlay(template, FIXTURE.read_bytes(), output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
