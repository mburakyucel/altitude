"""Approved v5 exposure gate: metadata/access predicates only, never kernel contents or writes."""
import errno
import json
import os
from pathlib import Path
import stat
import sys
import time

TOP = ('acpi','asound','bus','fs','irq','kallsyms','kcore','keys','latency_stats',
       'sched_debug','scsi','sys','sysrq-trigger','timer_list','timer_stats')


def inspect():
    started=time.monotonic()
    paths={Path('/proc')/name for name in TOP}
    def refused(error): raise error
    for base,dirs,files in os.walk('/proc/sys',followlinks=False,onerror=refused):
        for name in sorted(dirs+files):
            if len(paths)>=4096 or time.monotonic()-started>25:
                raise RuntimeError('Proc metadata inventory exceeded its bound')
            paths.add(Path(base)/name)
    rows=[]
    for path in sorted(paths):
        row={'path':str(path)}
        try:
            info=path.lstat()
            row.update(mode=stat.filemode(info.st_mode),uid=info.st_uid,gid=info.st_gid,
                       readable=os.access(path,os.R_OK,effective_ids=True),
                       writable=os.access(path,os.W_OK,effective_ids=True),
                       executable=os.access(path,os.X_OK,effective_ids=True))
        except OSError as error:
            if error.errno!=errno.ENOENT: raise
            row['absent']=True
        rows.append(row)
    mounts=[]
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields=line.split()
        if fields[4]=='/proc' or fields[4].startswith('/proc/'):
            mounts.append({'path':fields[4],'options':fields[5],'filesystem':fields[fields.index('-')+1:]})
    return {'uid':os.getuid(),'rows':rows,'mounts':mounts,
            'warning':'Access predicates only; no kernel contents read or tunables written.'}


def differences(result,policy):
    uid=str(result['uid'])
    if uid not in policy['writable']: return ['Unexpected probe principal']
    readable=set(policy['readable']) | (set(policy['root_extra_readable']) if uid=='0' else set())
    writable=set(policy['writable'][uid])
    return [row['path']+':'+access for row in result['rows'] for access,allowed in
            (('readable',readable),('writable',writable)) if row.get(access) and row['path'] not in allowed]


if __name__=='__main__':
    result=inspect()
    result['unexpected_exposure']=differences(result,json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(result))
    # The owner stores the complete evidence before failing the gate.
