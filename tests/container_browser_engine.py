#!/usr/bin/python3
"""Deterministic external CLI for the native container browser journey; no provider."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import uuid

prompt=sys.stdin.read()
control=json.loads((Path.home()/'.fixture-control.json').read_text())
session=sys.argv[-2] if 'resume' in sys.argv else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':session}),flush=True)
if control['intro_failure'] and not os.environ.get('ALTITUDE_TASK'):
    print(json.dumps({'type':'turn.failed','error':'Fixture authentication refused'}),flush=True)
    raise SystemExit(1)
if os.environ.get('ALTITUDE_TASK'):
    if not Path('fixture-draft.txt').exists():
        Path('fixture-draft.txt').write_text('Saved fictional task work.\n')
    with Path('fixture-input.jsonl').open('a') as output:
        output.write(json.dumps({'session':session,'prompt':prompt})+'\n')
    time.sleep(90)
else:
    if 'Create the fictional browser task.' in prompt:
        subprocess.run(['alt','task','new','--title','Browser fixture task','--hold-merge','Fixture review','-'],
            input='Fictional container task. Preserve the draft and wait for operator Stop. No external operations.\n',
            capture_output=True,text=True,check=True,timeout=20)
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message',
        'text':'Fictional container coordinator connected.'}}),flush=True)
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}),flush=True)
