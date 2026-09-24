# Contributing to Altitude

Altitude is early-stage software developed in the open. Start with the [README](README.md),
[setup](docs/SETUP.md) and [architecture](docs/ARCHITECTURE.md). The project rules that every
change follows, including the seven review questions, are in [AGENTS.md](AGENTS.md); it is
the same instruction file the coding agents read.

## License

The maintainer's license choice is pending and a `LICENSE` file records it when made. Until
then the repository grants no rights beyond GitHub's terms of service; read and evaluate it,
but do not redistribute it. Public release follows that decision, as tracked in the
[roadmap](docs/ROADMAP.md#early-user-onboarding-and-public-release).

## Ask, report or propose

Use the [issue templates](https://github.com/mburakyucel/altitude/issues/new/choose): a bug or
setup problem, or an idea. Describe the engineering problem, an example of when it occurs and
the outcome you want. For a bug, include the commit or installed version, environment, steps to
reproduce, and expected versus actual behavior. Setup friction and confusing product wording are
useful reports too.

Report security problems privately as described in the [security policy](SECURITY.md), never
in a public issue.

Discuss product or architecture changes in an issue before implementing them. Prefer small
coordination mechanisms with a clear owner and a current caller. Keep engine, operator and
optional local-service details at their named boundaries. Use the engines' native instructions,
skills, hooks and helpers where appropriate, and explain the need before introducing another
persistent stage, role or state.

## Develop and verify

Fork the repository, or work in a branch if you have write access, and open a pull request
against `main`. Keep each PR to one agreed change, preserve unrelated work and ship the
documentation that describes changed behavior in the same PR, in present tense. Altitude-owned
tasks work the same way through `alt land`, which publishes the PR and waits for its checks.

Every PR needs one successful run of the full suite on its exact head, with current `main`
included: Python and web tests, typecheck/build and isolated phone/desktop browser flows.

```sh
pnpm --dir web install --frozen-lockfile
# Install matching Chromium once, as described in Development and checks.
make check
```

[Development and checks](docs/DEVELOPMENT.md) covers temporary test state, restricted package
stores, Vite and Playwright. UI changes also need the specified interaction states walked at
phone and desktop sizes. Changes to dispatch, engines or landing need deterministic integration
evidence for the affected user flows and failure modes. Application logic, API/storage and Git
stay real; external engine and GitHub effects use fixtures. Live-provider validation, including
the real tiny task, is deferred under the operator's recorded testing policy.

A pull request from a fork, or from anyone other than the repository owner, runs that suite on a
GitHub-hosted runner with a read-only token and no secrets (`Hosted checks`); a failed run keeps
its browser report as a short-lived artifact for you to inspect. The required `check` itself runs
on the maintainer's self-hosted runner only for the maintainer's own branches. After review, the
maintainer integrates your contribution on a repository branch, where that required check runs
before the merge. Nothing merges without it, and there is no local bypass. See
[CI and candidate identity](docs/DEVELOPMENT.md#ci-and-candidate-identity).

In the PR, describe the problem and resulting behavior, answer the seven review questions in at
most fifteen lines, and include the relevant validation. Record material findings and their
dispositions. Describe what the evidence does and does not establish; do not replace behavioral
assertions with a line-coverage percentage. Add user-visible changes to
[Unreleased](CHANGELOG.md) in the same PR. Ordinary development does not restart deployed
services; see [operations](docs/OPERATIONS.md). [Release checkpoints](docs/RELEASING.md)
identify validated versions; publishing one is the maintainer's explicit decision.

## Share useful evidence

Use fictional projects and sanitized minimal reproductions. Live browser artifacts, task
conversations and incident records stay private. Do not attach credentials, tokens, full runtime
state or private session logs.
