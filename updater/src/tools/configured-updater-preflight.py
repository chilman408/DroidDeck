# SPDX-License-Identifier: GPL-3.0-only
"""Fixed configured-.215 acceptance probe: public staging and read-only proof.

No updater enrollment, restart, installation replay, private-key or data access.
The only new files are the exact signed PUBLIC updater sources/artifacts under
their separately owned staging directory. Not a public installer command.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import zipfile

FILE = Path('/root/droiddeck-update-check-20261024hp/droiddeck-0.1.1-rc.5-update.pyz')
PIN = '68e2015ead6a4dc3cbe6ab8c54088ea3d5c92552288a83797b62936d05a37499'
ENTRY_PIN = 'b36e64e3c68f535a823d9b94f90dad926196862c8cecb79c84474e4e251e9363'
PACKAGE = Path('/usr/local/share/droiddeck-installer-source-c0e3a5a/droiddeck-source-candidate')

def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or sys.argv[1:]: raise ValueError('Fixed root Linux configured-VM preflight required')
    os.umask(0o077); sys.dont_write_bytecode = True
    info = FILE.lstat()
    if info.st_uid != 0 or info.st_gid != 0 or info.st_nlink != 1 or info.st_mode != 0o100600:
        raise ValueError('Original root-private accepted package copy required')
    raw = FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN: raise ValueError('Exact reproduced signed package required')
    with zipfile.ZipFile(FILE) as archive: entry = archive.read('__main__.py')
    if hashlib.sha256(entry).hexdigest() != ENTRY_PIN: raise ValueError('Exact tested signature-first entry required')
    e = types.ModuleType('verified_update_preflight'); e.__file__ = str(FILE) + '/__main__.py'
    exec(compile(entry,e.__file__,'exec'),e.__dict__)
    entries = e.package_entries(FILE); release,_manifest,files = e.authenticate(entries)
    root = Path('/usr/local/share') / ('droiddeck-update-' + release['target']['sourceSha256'][:16])
    e.materialize(root,{**{'src/' + name: content for name,content in files.items()},
        **{name:entries[name] for name in ('source.tar','update.tar','statement.json','signature.bin')}})
    path = root/'src/runtime/portable/gateway-update-release.py'
    spec = importlib.util.spec_from_file_location('verified_update_preflight_sources',path)
    d = importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
    config = json.loads(d.G.protected_read(Path('/etc/droiddeck/runtime.json'),0o644,16384))
    if config['lanAddress'] != '192.168.2.215': raise ValueError('Only the already configured .215 preflight is authorized here')
    watch = 'droiddeck-fresh-gpu-watch@' + d.B.current_gpu() + '.service'
    before = {name:d.G.unit(name) for name in (d.B.RUNTIME,watch)}
    if any(x['ActiveState']!='active' or int(x['MainPID'])<2 for x in before.values()): raise ValueError('Existing healthy runtime/watcher required')
    proof = d.B.A.base_proof('completed',str(PACKAGE))
    if proof.get('originalInstallationOwnershipVerified') is not True or proof.get('completedPhasesVerified')!=35:
        raise ValueError('Actual original completed configured-RC2 authority required')
    if {name:d.G.unit(name) for name in before}!=before: raise ValueError('Original runtime or watcher changed during read-only proof')
    if os.path.lexists(d.ENROLLMENT) or os.path.lexists(d.B.CONTROLLER) or os.path.lexists(d.B.A.TRUST):
        raise ValueError('Preflight must not enroll public key, policy, controller or services')
    print(json.dumps({'kind':'configured-rc2-updater-preflight','originalInstallationOwnershipVerified':True,
        'completedPhasesVerified':35,'baseProofSha256':d.P.sha(d.P.encode(proof)),
        'signedSourceAndPayloadVerified':True,'updateTrustEnrolled':False,'controllerEnrolled':False,
        'originalRuntimeAndGpuWatcherUnchanged':True,'androidDataRead':False,'tlsPrivateKeyRead':False},sort_keys=True),flush=True)

if __name__=='__main__':main()
