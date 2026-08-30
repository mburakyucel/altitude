# Project rules — altitude

*Ledger (decision 14/32). Each entry: five fields + the rule text that gets compiled into briefs and personas. Project scope: these apply to work in this repo only; global and stack rules live in `rules/`.*

## R-002 — reserve a launch for the mandated reviewer
- scope: project
- where: CLAUDE.md
- origin: I-004 (altitude)
- prevents: 
- effect: 
- status: probation
- text: Size the launch budget so the verification a brief mandates always fits: subagent_launches = expected implementers + 1 reserved for the reviewer (an S task with one L1 gets 3, not 2). The reserved launch is spent on nothing else, and capability probes are not launches. A reviewer that cannot run because the budget is full is a dispatch-time sizing bug, not a mid-run block.
