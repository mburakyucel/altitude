#!/usr/bin/env python3
"""Copy fictional Playwright captures into the committed conversation-first review gallery.

Run after: pnpm --dir web ui conversation-first-wireframes.pw.ts
Use --implementation after the real application's conversation-decisions walkthrough.
The normal Playwright artifact directory remains ignored; no live captures are imported.
"""
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parents[1] / 'web/ui-artifacts/results'
OUT = HERE / 'conversation-first/captures'
MAIN = {
    'needs-you': '01-needs-you', 'single': '02-single', 'group': '03-group',
    'followup': '04-followup', 'partial': '05-partial', 'accepted': '06-accepted',
}

implementation = '--implementation' in sys.argv[1:]
if implementation:
    OUT = HERE / 'conversation-first/implementation'
    MAIN = {
        'needs-you': '01-needs-you-recommendation',
        'single': 'review-02-single',
        'group': 'review-03-group',
        'followup': '03b-follow-up-exchange',
        'partial': 'review-05-partial',
        'accepted': 'review-06-accepted',
    }

copies = []
for viewport in ('phone', 'desktop'):
    prefix = 'conversation-decisions.pw.' if implementation else 'conversation-first-wirefra'
    folders = list(RESULTS.glob(f'{prefix}-*-{viewport}'))
    for name, source in MAIN.items():
        candidates = [folder / f'{source}.png' for folder in folders if (folder / f'{source}.png').is_file()]
        if len(candidates) != 1:
            raise SystemExit(f'Expected one {viewport}/{source}.png; run the matching walkthrough first.')
        copies.append((candidates[0], OUT / f'{viewport}-{name}.png'))

OUT.mkdir(parents=True, exist_ok=True)
expected = {target for _, target in copies}
for obsolete in OUT.glob('*.png'):
    if obsolete not in expected:
        obsolete.unlink()
for source, target in copies:
    shutil.copyfile(source, target)
print(f'Copied {len(copies)} fictional phone/desktop captures to {OUT.relative_to(HERE)}')
