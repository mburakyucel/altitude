You are building Altitude's native macOS runtime on this Mac (Apple silicon), in close loop with its owner task on the Linux machine.

Setup: you are on branch `mac-runtime`. The approved design is `macos-handoff/proposal.md`, the Linux-assumption audit is
`macos-handoff/audit.md`, and the provider-free probe is `macos-handoff/mac_probe_0.py`. This directory is a temporary
handoff; the owner task deletes it before the branch lands. Probe output and notes go in `~/altitude-mac-notes/`, outside git.
- Read `AGENTS.md`, `docs/ARCHITECTURE.md` and `altitude/platform.py` before changing anything.

Order of work:
1. Run `mkdir -p ~/altitude-mac-notes && python3 macos-handoff/mac_probe_0.py > ~/altitude-mac-notes/probe0.json` with the system python3. Read the result against each row of proposal.md. For any failed item, stop and write the failure and a proposed fallback in `~/altitude-mac-notes/findings.md`. Do not work around it silently.
2. Get the existing suite running natively (`make check-python`). Record the macOS prerequisites you actually needed in findings.md.
3. Implement increment 2 from proposal.md behind `altitude/platform.py` only. It covers the LaunchAgent service, jobs as their own launchd jobs, coalition-based Stop, the Seatbelt profile, the terminal's process and socket facts, install/update/uninstall, and the Mac branch of `scripts/install.sh`. Linux behavior must stay unchanged. Add fixture tests that also pass on Linux, and add `scripts/platform_probe.py`.
4. Commit in small, clearly described commits on `mac-runtime`. Push the branch (`git push origin mac-runtime`). Do not open or merge a PR; the Linux owner task reviews and lands it.

Limits:
- No sudo or admin prompts.
- No live-provider or real-engine Altitude runs in this session. Tests use fixture engines only. Live-provider testing happens only under a separate operator decision, arranged by the Linux owner task, not by this session.
- No model downloads.
- Do not install Altitude as a persistent service in this account. Any LaunchAgent you test uses a throwaway label and `ALTITUDE_HOME` under /tmp, and you remove it afterwards.
- Never commit probe output, credentials, Keychain details, hostnames or other personal data.
- Keep `~/altitude-mac-notes/findings.md` current: what works, what failed, the prerequisites, and anything that differs from proposal.md.
- When you stop, push, then summarize findings.md (without personal data) in `macos-handoff/STATUS.md` and push that too, so the owner task can read where you are.
