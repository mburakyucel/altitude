# Contributing to Altitude

Altitude is an early private preview for invited collaborators. Start with the
[README](README.md), [setup](docs/SETUP.md), and [architecture](docs/ARCHITECTURE.md).
The maintainer has not selected a license; public redistribution and a public contribution
process are [release prerequisites](docs/ROADMAP.md#early-user-onboarding-and-public-release).

## Propose a small, concrete change

Use the repository's Issues page (linked under [Feedback](README.md#feedback)) to describe the
engineering problem, an example of when it occurs, and the outcome you want. For a bug, include
the commit, environment, steps to reproduce, and expected versus actual behavior. For a larger
product or architecture change, discuss the approach with the maintainer before implementation.

Prefer small coordination mechanisms with a clear owner and a current caller. Keep engine,
operator and optional local-service details at their named boundaries. Use the engines' native
instructions, skills, hooks and helpers where appropriate; explain the need before introducing
another persistent stage, role or state. The project rules and seven review questions are in
[CLAUDE.md](CLAUDE.md) and the [simplification record](docs/SIMPLIFICATION.md#working-rules-that-still-apply-to-every-pr).

## Develop and verify

Work in an isolated branch/worktree and deliver through a PR. Preserve other work and keep
changes within the agreed scope. Include the documentation that describes changed behavior in
the same PR, in present tense. For Altitude-owned tasks, use the declared file lease and
`alt land --message "..."`; add `--merge` only after checks/review and when no merge hold exists.
Invited human contributors should agree branch access with the maintainer; the current CI path
tests branches in this repository and does not accept private-fork PRs.

Every PR runs the full Python and web unit suites; build the UI to check TypeScript and bundling:

```sh
make test
pnpm --dir web install --frozen-lockfile
pnpm --dir web test
pnpm --dir web build
```

[Development and checks](docs/DEVELOPMENT.md) covers temporary test state, restricted package
stores, Vite and Playwright. UI changes also need the specified interaction states walked at
phone and desktop sizes. Changes to dispatch, engines or landing need a real tiny task through
chat, PR, checks, merge and archive, as required by the project rules.

In the PR, describe the problem and resulting behavior, answer the seven review questions in at
most fifteen lines, and include the relevant validation. Record material findings and their
dispositions. CI currently runs Python; passing CI alone does not replace the local web suite.
Ordinary development does not restart deployed services; see [operations](docs/OPERATIONS.md).

## Share useful evidence

Use fictional projects and sanitized minimal reproductions. Live browser artifacts, task
conversations and incident records stay private. Do not attach credentials, tokens, full runtime
state or private session logs. Report sensitive issues privately through the maintainer contact
used for your invitation; a public security-reporting policy is still a release prerequisite.
