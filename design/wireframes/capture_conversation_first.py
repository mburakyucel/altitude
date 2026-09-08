#!/usr/bin/env python3
"""Copy only this proposal's fictional Playwright captures into its committed review gallery.

Run after: pnpm --dir web ui conversation-first-wireframes.pw.ts
The normal Playwright artifact directory remains ignored; no live captures are imported.
"""
from pathlib import Path
import shutil

from conversation_first import STATES

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parents[1] / 'web/ui-artifacts/results'
OUT = HERE / 'conversation-first/captures'
MAIN = {
    'needs-you': '01-needs-you', 'question': '02-question',
    'followup-answer': '03-followup-answer', 'alternative-draft': '05-alternative-draft',
    'typed-decision': '06-typed-decision', 'quick-acceptance': '07-quick-acceptance',
    'resumed': '08-resumed', 'resolved-elsewhere': '10-resolved-elsewhere',
    'simple-answer': '11-simple-answer',
}

copies = []
for viewport in ('phone', 'desktop'):
    folders = list(RESULTS.glob(f'conversation-first-wirefra-*-{viewport}'))
    for name, source in {**MAIN, **{f'state-{state}': state for state in STATES}}.items():
        candidates = [folder / f'{source}.png' for folder in folders if (folder / f'{source}.png').is_file()]
        if len(candidates) != 1:
            raise SystemExit(f'Expected one {viewport}/{source}.png; run the proposal walkthrough first.')
        copies.append((candidates[0], OUT / f'{viewport}-{name}.png'))

OUT.mkdir(parents=True, exist_ok=True)
for source, target in copies:
    shutil.copyfile(source, target)
print(f'Copied {len(copies)} fictional phone/desktop captures to {OUT.relative_to(HERE)}')
