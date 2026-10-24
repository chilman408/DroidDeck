# SPDX-License-Identifier: GPL-3.0-only
"""Generate a separate, version-pinned Trebuchet resource overlay, not an APK patch."""
import argparse
import hashlib
import struct
from pathlib import Path
from zipfile import ZipFile

from androguard.core.axml import AXMLParser, END_DOCUMENT, START_TAG, TYPE_INT_BOOLEAN
from loguru import logger

ORIGINAL_SHA256 = "a3ebae0cba0c0abdffc2eed8398068867ab962fc4b768c09ef0a5182788cc24e"
RESOURCE = "res/xml/device_profiles.xml"
NAMESPACE = "http://schemas.android.com/apk/res-auto"


def disable_scalable_tablet_grid(resource):
    if hashlib.sha256(resource).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Unsupported Trebuchet grid resource; nothing changed")
    result = bytearray(resource)
    parser = AXMLParser(bytes(resource))
    changed = 0
    while parser.is_valid():
        event = next(parser)
        if event == END_DOCUMENT:
            break
        if event != START_TAG or parser.name != "grid-option":
            continue
        count = parser.getAttributeCount()
        attributes = {parser.getAttributeName(i): i for i in range(count)
                      if parser.getAttributeNamespace(i) == NAMESPACE}
        name = attributes.get("name")
        if name is None or parser.getAttributeValue(name) != "6_by_5":
            continue
        index = attributes.get("isScalable")
        if index is None or parser.at_size != 20 or \
                parser.getAttributeValueType(index) != TYPE_INT_BOOLEAN or \
                parser.getAttributeValueData(index) != 0xFFFFFFFF:
            raise ValueError("Unexpected tablet scaling attribute")
        # Androguard has consumed this tag's attribute array. Keep its compiled
        # resource IDs/string pool intact; change only the validated boolean data.
        offset = parser.buff.tell() - count * parser.at_size + index * parser.at_size
        if offset < 0 or offset + 20 > len(resource) or \
                resource[offset + 15] != TYPE_INT_BOOLEAN or \
                struct.unpack_from("<I", resource, offset + 16)[0] != 0xFFFFFFFF:
            raise ValueError("Unexpected compiled attribute position")
        struct.pack_into("<I", result, offset + 16, 0)
        changed += 1
    if not parser.is_valid() or changed != 1:
        raise ValueError("Expected exactly one valid tablet grid")
    return bytes(result)


def package_overlay(template, resource, output):
    modified = disable_scalable_tablet_grid(resource)
    with ZipFile(template) as source:
        names = source.namelist()
        if len(set(names)) != len(names) or set(names) != {
                "AndroidManifest.xml", "resources.arsc", RESOURCE}:
            raise ValueError("Expected unsigned resource-only overlay template")
        if source.getinfo(RESOURCE).file_size > 4096:
            raise ValueError("Unexpected overlay placeholder")
        entries = [(info, modified if info.filename == RESOURCE else source.read(info))
                   for info in source.infolist()]
    with ZipFile(output, "x") as target:
        for info, value in entries:
            target.writestr(info, value)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("apk", type=Path)
    args.add_argument("output", type=Path)
    args.add_argument("--template", type=Path, help="Unsigned resource-only overlay APK")
    options = args.parse_args()
    logger.disable("androguard")
    with ZipFile(options.apk) as apk:
        info = apk.getinfo(RESOURCE)
        if info.file_size != 9952:
            raise ValueError("Unsupported Trebuchet resource size")
        original = apk.read(info)
        modified = disable_scalable_tablet_grid(original)
    if options.template:
        package_overlay(options.template, original, options.output)
        print("Created separate unsigned resource-only overlay APK")
        return
    with options.output.open("xb") as output:
        output.write(modified)
    print("Created separate tablet-grid resource: " + hashlib.sha256(modified).hexdigest())


if __name__ == "__main__":
    main()
