# SPDX-License-Identifier: GPL-3.0-only
import hashlib,json,os,stat,sys
from pathlib import Path
BASE=Path('/home/hilman/teslaandroid-waydroid/frontend/js/display')
BACK=Path('/var/lib/teslaandroid-waydroid/backups/browser-legacy-policy-20261024ht')
ROOT=Path('/opt/teslaandroid-waydroid/browser-legacy-policy-20261024ht')
BEFORE='d086e1f915e01d80520e6b0d71dc4fa05614b5b52e17a16bce584cff8ef38527'
POLICY='eab401e04cf464d4e2a970165531090629d95d9f0cbdca09c8b1521619ff7b98'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def protected(path):
    i=path.lstat()
    if not stat.S_ISREG(i.st_mode) or i.st_uid!=0 or i.st_gid!=0 or i.st_nlink!=1 or i.st_mode&0o022:raise ValueError('Protected source required')
def save(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:os.write(fd,raw);os.fsync(fd)
    finally:os.close(fd)
def main(args):
    if args not in (['--prepare'],['--apply'],['--restore']) or os.getuid()!=0:raise ValueError('Fixed root operation required')
    if Path(__file__)!=ROOT/'preview-policy-backport.py' or Path('/etc/hostname').read_text().strip()!='Waydroid-TeslaAndroid-Test':raise ValueError('Exact preview required')
    protected(Path(__file__));protected(ROOT/'browser-renderer-policy.js')
    policy=(ROOT/'browser-renderer-policy.js').read_bytes()
    if sha(policy)!=POLICY:raise ValueError('Reviewed policy required')
    directory=os.open(BASE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=os.open('display-renderer-manager.js',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
        try:
            info=os.fstat(fd);raw=os.read(fd,262145)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid not in (0,1000) or stat.S_IMODE(info.st_mode)!=0o644 or len(raw)>262144:raise ValueError('Fixed regular public asset required')
        finally:os.close(fd)
        if args==['--restore']:
            protected(BACK/'original.js');protected(BACK/'receipt.json')
            original=(BACK/'original.js').read_bytes();receipt=json.loads((BACK/'receipt.json').read_bytes())
            if sha(original)!=BEFORE or sha(raw)!=receipt['after']:raise ValueError('Foreign asset retained')
            target=original
        else:
            if BACK.exists() or BACK.is_symlink() or sha(raw)!=BEFORE:raise ValueError('Unchanged preview and new backup required')
            target=raw+b'\n;\n'+policy
            if args==['--prepare']:
                print(json.dumps({'readOnlyPreviewPolicyPreflight':True,'after':sha(target)}));return
            BACK.mkdir(mode=0o700)
            save(BACK/'original.js',raw)
            save(BACK/'receipt.json',(json.dumps({'profile':'browser-legacy-policy-20261024ht','before':BEFORE,'after':sha(target)})+'\n').encode())
        pending='pending-browser-legacy-policy-20261024ht'+('-restore' if args==['--restore'] else '')
        out=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            if os.write(out,target)!=len(target):raise ValueError('Incomplete source write')
            os.fchown(out,info.st_uid,info.st_gid);os.fchmod(out,stat.S_IMODE(info.st_mode));os.fsync(out)
        finally:os.close(out)
        current=os.stat('display-renderer-manager.js',dir_fd=directory,follow_symlinks=False)
        if (current.st_dev,current.st_ino,current.st_mtime_ns,current.st_size)!=(info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size):raise ValueError('Changed asset retained')
        os.rename(pending,'display-renderer-manager.js',src_dir_fd=directory,dst_dir_fd=directory);os.fsync(directory)
        print(json.dumps({'previewPolicy': 'restored' if args==['--restore'] else 'installed','sha256':sha(target),'runtimeRestarted':False,'androidDataChanged':False}))
    finally:os.close(directory)
if __name__=='__main__':main(sys.argv[1:])
