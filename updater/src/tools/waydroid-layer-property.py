# SPDX-License-Identifier: GPL-3.0-only
"""Offline, fixed-key edit of Android's saved Waydroid layer property."""
import os
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError

KEY = 'persist.waydroid.use_subsurface'
MAX_BYTES = 65536


def document_type():
    # Android init/persistent_properties.proto: proto2 repeated records (name, value).
    schema = descriptor_pb2.FileDescriptorProto(name='tesla_persistent_properties.proto', syntax='proto2')
    document = schema.message_type.add(name='PersistentProperties')
    record = document.nested_type.add(name='PersistentPropertyRecord')
    for number, name in [(1, 'name'), (2, 'value')]:
        record.field.add(name=name, number=number, label=1, type=9)
    document.field.add(name='properties', number=1, label=3, type=11,
                       type_name='.PersistentProperties.PersistentPropertyRecord')
    pool = descriptor_pool.DescriptorPool()
    pool.Add(schema)
    descriptor = pool.FindMessageTypeByName('PersistentProperties')
    if hasattr(message_factory, 'GetMessageClass'):
        return message_factory.GetMessageClass(descriptor)
    return message_factory.MessageFactory(pool).GetPrototype(descriptor)


PropertyDocument = document_type()


def parse(data, *, allow_missing=False):
    if type(allow_missing) is not bool:
        raise ValueError('Closed missing-property policy required')
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Invalid saved property document size')
    document = PropertyDocument()
    try:
        document.ParseFromString(data)
    except (DecodeError, UnicodeError):
        raise ValueError('Invalid saved property document') from None
    if (not document.properties and not allow_missing) or any(not p.HasField('name') or not p.name or not p.HasField('value')
                                      for p in document.properties):
        raise ValueError('Incomplete saved property record')
    selected = [p for p in document.properties if p.name == KEY]
    if not selected and allow_missing:
        return document, None
    if len(selected) != 1 or selected[0].value not in ('false', 'true'):
        raise ValueError('Expected one fixed boolean layer property')
    return document, selected[0]


def rewrite(data, expected, target):
    if not expected or any(item not in ('false', 'true') for item in expected) or target not in ('false', 'true'):
        raise ValueError('Fixed boolean property transition required')
    document, record = parse(data)
    if record.value not in expected:
        raise ValueError('Saved layer property changed')
    if record.value == target:
        return data
    record.value = target
    return document.SerializeToString()


def saved_value(data):
    _document, record = parse(data, allow_missing=True)
    return record.value if record is not None else 'absent'


def rewrite_optional(data, expected, target):
    if not expected or any(item not in ('absent', 'false', 'true') for item in expected) or target not in ('absent', 'false', 'true'):
        raise ValueError('Closed optional boolean transition required')
    document, record = parse(data, allow_missing=True)
    before = record.value if record is not None else 'absent'
    if before not in expected:
        raise ValueError('Saved layer property changed')
    if before == target:
        return data
    if target == 'absent':
        document.properties.remove(record)
    elif record is None:
        document.properties.add(name=KEY, value=target)
    else:
        record.value = target
    return document.SerializeToString()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class OfflineStore:
    def __init__(self, path, protected, assert_stopped, atomic_save):
        self.path = path
        self.protected = protected
        self.assert_stopped = assert_stopped
        self.atomic_save = atomic_save

    def read(self):
        self.protected(self.path.parent, directory=True)
        before = self.protected(self.path)
        fd = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
                raise RuntimeError('Saved property file changed before read')
            data = stream.read(MAX_BYTES + 1)
        if identity(before) != identity(self.protected(self.path)):
            raise RuntimeError('Saved property file changed during read')
        parse(data)
        return data, before

    def value(self):
        data, _ = self.read()
        return parse(data)[1].value

    def set_value(self, expected, target):
        self.assert_stopped()
        data, before = self.read()
        changed = rewrite(data, expected, target)
        self.assert_stopped()
        self.protected(self.path.parent, directory=True)
        if identity(before) != identity(self.protected(self.path)):
            raise RuntimeError('Saved property file changed before write')
        if changed != data:
            self.atomic_save(self.path, changed, 0o600)
