"""Disposable-VM observation/faults around the real transfer worker, never a product mode."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from unittest import mock

from altitude import platform
from scripts import container

SENTINEL=b'backup-secret-sentinel-7392'


def worker(record: Path, mode: str) -> dict:
    operation=json.loads(record.read_text())
    actual=platform.container_binary
    audit={'mode':mode,'helper_logs':[]}

    def binary(arguments,**kwargs):
        is_helper=arguments[0]=='start'
        if is_helper and mode=='disk-limit': kwargs['max_bytes']=1024
        result=actual(arguments,**kwargs)
        if is_helper:
            value=json.loads(platform.container_command(['inspect',operation['helper']]))[0]
            logs=subprocess.run(platform.container_arguments(['logs',operation['helper']]),
                env=platform.container_user_environment(),capture_output=True,timeout=10)
            assert not logs.stdout and SENTINEL not in logs.stderr
            paths=[value.get('LogPath',''),value['HostConfig']['LogConfig'].get('Path','')]
            for path in paths:
                assert not path or not Path(path).exists() or not Path(path).read_bytes()
            assert value['HostConfig']['LogConfig']['Type']=='none'
            audit['helper_logs'].append({'driver':'none','stdout_bytes':len(logs.stdout),
                'log_command_exit':logs.returncode,'store_log_absent_or_empty':True})
            if mode=='truncate':
                kwargs['target'].truncate(512)  # native helper exited0, transport delivered incomplete bytes
            elif mode=='crash-worker':
                os.kill(os.getpid(),signal.SIGKILL)
            elif mode=='wait-for-client-death':
                (record.parent/'fixture-ready').write_text('helper finished; waiting for the finite fixture kill\n')
                time.sleep(120)  # parent kills only this recorded unit; its deadline remains active
        return result

    try:
        with mock.patch.object(platform,'container_binary',side_effect=binary):
            result=container.transfer_worker(record)
        assert SENTINEL not in json.dumps(result).encode()
        return result
    finally:
        path=record.parent/'fixture-audit.json'
        path.write_text(json.dumps(audit)); path.chmod(0o600)
