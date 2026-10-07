"""Fictional image only: real daemon/timers, external engine/tool observations replaced."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/opt/altitude')
from altitude import config, engines, installation, monitor, server

CONTROL=Path.home()/'.fixture-control.json'


def control():
    return json.loads(CONTROL.read_text())


def prepare():
    if CONTROL.exists(): return
    CONTROL.write_text(json.dumps({'installed':False,'signed_in':False,'intro_failure':True}))
    CONTROL.chmod(0o600)
    root=Path.home()/'Projects'
    for name in ('atlas','custom'):
        repo=root/name; repo.mkdir()
        def git(*args):
            subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True,timeout=15)
        git('init','-q','-b','main')
        git('config','user.name','Fixture Operator')
        git('config','user.email','fixture@example.invalid')
        git('config','commit.gpgsign','false')
        (repo/'AGENTS.md').write_text('Fictional browser acceptance. No external operations.\n')
        (repo/'README.md').write_text('Fictional repository.\n')
        git('add','.'); git('commit','-qm','Fictional initial commit')
        remote=root/(name+'-origin.git')
        git('init','-q','--bare',str(remote))
        git('remote','add','origin',str(remote)); git('push','-qu','origin','main')
        if name=='custom':
            hook=repo/'.git/hooks/pre-commit'
            hook.write_text('#!/bin/sh\nexit 0\n'); hook.chmod(0o755)
    (root/'notes').mkdir()
    (root/'notes/README.md').write_text('Conversation-only fixture.\n')


def main():
    prepare()
    config.CODEX_BIN='/opt/fixture/engine'
    engines.installation=lambda engine: {'available': engine=='codex' and control()['installed'],
        'why':'deterministic fixture installation observation'}
    engines.sign_in=lambda engine: {'signed_in':engine=='codex' and control()['signed_in'],
        'command':engines.SIGN_IN[engine][1]}
    engines.usage_hold=lambda *args,**kwargs: None
    engines.claude_agents=lambda: []
    engines.refresh_quotas=lambda: None
    monitor.quota=lambda: {'known':True}
    installation._gh_signed_in=lambda: control()['signed_in']
    server.main()


if __name__=='__main__': main()
