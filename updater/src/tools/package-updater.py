# SPDX-License-Identifier: GPL-3.0-only
"""Reproducible public single-file signed updater packaging, no installation."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import zipfile

def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);v=importlib.util.module_from_spec(s);s.loader.exec_module(v);return v

E=load('standalone_update_entry',Path(__file__).with_name('update-installer-entry.py'))
P=load('standalone_update_package',Path(__file__).parents[1]/'runtime/portable/update-package.py')

def build(source,bundle,statement,signature,launcher):
    entries={'source.tar':source,'update.tar':bundle,'statement.json':statement,'signature.bin':signature}
    release,_manifest,files=E.authenticate(entries)
    P.review(bundle,P.sha(bundle))
    with tarfile.open(fileobj=io.BytesIO(bundle),mode='r:') as archive: manifest_raw=archive.extractfile(P.MANIFEST).read()
    manifest=P.document(manifest_raw)
    if (P.sha(manifest_raw)!=release['manifestSha256']
            or any(manifest[k]!=release[k] for k in ('base','target','platform','dataPolicy'))
            or files.get('tools/update-installer-entry.py')!=launcher
            or 'runtime/portable/gateway-update-release.py' not in files):
        raise ValueError('Exact signed manifest, committed launcher and public bootstrap required')
    entries['__main__.py']=launcher
    output=io.BytesIO();output.write(b'#!/usr/bin/env python3\n')
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,raw in sorted(entries.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=0o100644<<16;info.compress_type=zipfile.ZIP_STORED
            archive.writestr(info,raw)
    data=output.getvalue()
    return data,{'kind':'droiddeck-single-file-updater','target':release['target'],'sha256':P.sha(data),
        'sourceAndPayloadAuthenticated':True,'releaseKeySha256':E.KEY_PIN,'privateKeyIncluded':False,
        'androidDataIncluded':False,'payloadExecuted':False,'servicesChanged':False,'installable':False}

def main(args):
    if len(args)!=5:raise ValueError('SOURCE_TAR UPDATE_TAR STATEMENT_JSON SIGNATURE_BIN NEW_PYZ')
    source,bundle,statement,signature=[P.read_file(Path(name),P.MAX_ARCHIVE) for name in args[:4]]
    data,report=build(source,bundle,statement,signature,Path(__file__).with_name('update-installer-entry.py').read_bytes())
    fd=os.open(args[4],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(os.dup(fd),'wb') as stream:stream.write(data);stream.flush()
        os.fsync(fd)
    finally:os.close(fd)
    print(json.dumps(report,sort_keys=True))

if __name__=='__main__':main(sys.argv[1:])
