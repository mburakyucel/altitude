#!/usr/bin/python3
"""Deterministic container browser CLI; coordinator exec and worker app-server, no provider."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import uuid


def out(message):
    print(json.dumps(message),flush=True)


def notify(method,params):
    out({'method':method,'params':params})


def coordinator(prompt):
    if 'Create the fictional browser task.' in prompt:
        subprocess.run(['alt','task','new','--title','Browser fixture task','--hold-merge','Fixture review','-'],
            input='Fictional container task. Preserve the draft and wait for operator Stop. No external operations.\n',
            capture_output=True,text=True,check=True,timeout=20)
    return 'Fictional container coordinator connected.'


control=json.loads((Path.home()/'.fixture-control.json').read_text())
if sys.argv[1:2]==['exec']:
    prompt=sys.stdin.read()
    session=sys.argv[-2] if 'resume' in sys.argv else str(uuid.uuid4())
    out({'type':'thread.started','thread_id':session})
    if control['intro_failure']:
        out({'type':'turn.failed','error':'Fixture authentication refused'})
        raise SystemExit(1)
    out({'type':'item.completed','item':{'type':'agent_message','text':coordinator(prompt)}})
    out({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}})
    raise SystemExit(0)
for raw in sys.stdin:
    message=json.loads(raw)
    method,identity,params=message.get('method'),message.get('id'),message.get('params') or {}
    if method=='initialize':
        out({'id':identity,'result':{}})
    elif method in ('thread/start','thread/resume'):
        session=params.get('threadId') or str(uuid.uuid4())
        out({'id':identity,'result':{'thread':{'id':session}}})
    elif method=='turn/start':
        prompt=''.join(item.get('text','') for item in params['input'])
        out({'id':identity,'result':{'turn':{'id':'turn-1','status':'inProgress'}}})
        notify('turn/started',{'turn':{'id':'turn-1'}})
        if control['intro_failure'] and not os.environ.get('ALTITUDE_TASK'):
            notify('turn/completed',{'turn':{'id':'turn-1','status':'failed',
                'error':{'message':'Fixture authentication refused'}}})
        elif os.environ.get('ALTITUDE_TASK'):
            if not Path('fixture-draft.txt').exists():
                Path('fixture-draft.txt').write_text('Saved fictional task work.\n')
            with Path('fixture-input.jsonl').open('a') as output:
                output.write(json.dumps({'session':session,'prompt':prompt})+'\n')
            time.sleep(90)
            raise SystemExit(0)
        else:
            notify('item/completed',{'item':{'type':'agentMessage','id':'message',
                'text':coordinator(prompt)}})
            notify('thread/tokenUsage/updated',{'tokenUsage':{'total':{'inputTokens':1,'outputTokens':1}}})
            notify('turn/completed',{'turn':{'id':'turn-1','status':'completed'}})
