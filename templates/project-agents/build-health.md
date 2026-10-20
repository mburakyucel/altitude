name: Build health
days: Mon
time: 08:00
tell: found
summary: Runs the project's checks on a clean copy and says what broke

Read the project's own instructions (README, CONTRIBUTING, AGENTS.md or a Makefile) to find how its
checks run, then run them on this clean copy. Report each check that fails with the shortest excerpt
that shows why, and any step that became noticeably slower than the project says it should be. If
everything passes, say so in one line. Do not change the project's files beyond what the checks write.
