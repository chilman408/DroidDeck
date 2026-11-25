# SPDX-License-Identifier: GPL-3.0-only
"""Fresh operator enrollment adapter: no worker changes or private material copies."""
from pathlib import Path
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

def main():
    if os.geteuid()!=0 or len(sys.argv)!=4:raise ValueError('Root protected input directory, Git source revision, package digest required')
    root=Path(sys.argv[1]);revision,digest=sys.argv[2:]
    pins=json.loads((root/'operator-inputs.json').read_bytes())
    required={'bootstrap.py','package.tar','statement.json','signature.bin','recovery.service'}
    if set(pins)!=required:raise ValueError('Closed operator input map required')
    for name,row in pins.items():
        p=root/name;s=p.lstat();raw=p.read_bytes()
        if p.is_symlink() or s.st_uid!=0 or s.st_gid!=0 or s.st_mode&0o777!=0o600 or s.st_nlink!=1 or len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
            raise ValueError('Previously verified exact operator code inputs required')
    spec=importlib.util.spec_from_file_location('operator_bootstrap',root/'bootstrap.py')
    B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
    B.digest(digest);B.authenticate((root/'package.tar').read_bytes(),(root/'statement.json').read_bytes(),
        (root/'signature.bin').read_bytes(),B.TRUST.read_bytes())
    if B.SELF.parent.exists() or os.path.lexists(B.SERVICE):raise ValueError('Fresh loader/service enrollment only; never replay')
    with ExitStack() as stack:
        tree=B.Tree(stack);parent=tree.directory(B.SELF.parent.parent)
        os.mkdir(B.SELF.parent.name,0o700,dir_fd=parent);os.fsync(parent);fd=tree.directory(B.SELF.parent)
        B.write_new(fd,'bootstrap.py',(root/'bootstrap.py').read_bytes())
        service_fd=tree.directory(B.SERVICE.parent);B.write_new(service_fd,B.SERVICE.name,(root/'recovery.service').read_bytes());os.chmod(B.SERVICE,0o644);os.fsync(service_fd)
        program=tree.file(B.SELF,1048576);unit=tree.file(B.SERVICE,8192,0o644)
        receipt={'schema':1,'kind':'operator-pinned-capture-loader','sourceRevision':revision,
            'files':{'bootstrap.py':{'sha256':B.sha(program),'generation':B.generation(B.SELF.lstat())},
                'recoveryTemplate':{'sha256':B.sha(unit),'generation':B.generation(B.SERVICE.lstat())}}}
        B.write_new(fd,'complete.json',B.encode(receipt))
    # Separate process rechecks the exact operator enrollment before code staging.
    argv=['/usr/bin/python3','-I','-B',str(B.SELF)]
    subprocess.run(argv+['stage',str(root/'package.tar'),str(root/'statement.json'),str(root/'signature.bin'),digest],check=True,timeout=60)
    subprocess.run(argv+['inspect',digest],check=True,timeout=60)
    subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True,timeout=15)
    subprocess.run(['/usr/bin/systemctl','enable','droiddeck-capture-recover@'+digest+'.service'],check=True,timeout=15)
    print(json.dumps({'operatorLoaderEnrolled':True,'signedControlsStaged':True,'recoveryOnBootEnrolled':True,
        'servicesActivated':False,'packageSha256':digest,'sourceRevision':revision}))


if __name__=='__main__':main()
