#!/usr/bin/python3
"""Deterministic external CLI for the native container browser journey; no provider."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

prompt=sys.stdin.read()
control=json.loads((Path.home()/'.fixture-control.json').read_text())
if control['intro_failure'] and not os.environ.get('ALTITUDE_TASK'):
    print('Fixture authentication refused',file=sys.stderr)
    raise SystemExit(1)
session=sys.argv[-2] if 'resume' in sys.argv else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':session}),flush=True)
if os.environ.get('ALTITUDE_TASK'):
    Path('fixture-draft.txt').write_text('Saved fictional task work.\n')
    time.sleep(90)
else:
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message',
        'text':'Fictional container coordinator connected.'}}),flush=True)
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}),flush=True)
