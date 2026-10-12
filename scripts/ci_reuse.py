#!/usr/bin/env python3
"""Find the pull request run that already tested the exact tree of a push to main (#777).

Merging an up-to-date pull request pushes a commit whose tree is the PR head's tree, which the PR's run of
the same workflow has just tested in full. The `reuse` job of hosted-checks.yml runs this script for a push
to main: it writes `run=<id>` to GITHUB_OUTPUT only when every condition below holds, and on any miss,
error or doubt it prints why and writes nothing, so the shards run the full suite.
"""
import json
import os
import subprocess
import sys
import urllib.request

WORKFLOW = ".github/workflows/hosted-checks.yml"
CHECK = "check"
# The files that decide how CI runs. A push changing any of them runs in full, so a reused result only ever
# comes from a CI setup already proven on main. This script is one of them.
CONTRACT = (".github/", "Makefile", "tests/run_parallel.py", "web/package.json", "web/playwright.config.ts",
            "web/vite.config.ts", "scripts/time_command.py", "scripts/ci_reuse.py")


class NoReuse(Exception):
    pass


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def api(path: str):
    request = urllib.request.Request(
        f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}/{path}",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def contract_changes(paths: list[str]) -> list[str]:
    return [path for path in paths
            if any(path == item or (item.endswith("/") and path.startswith(item)) for item in CONTRACT)]


def reusable_run(repo: str, sha: str, before: str, *, api=api, git=git) -> dict:
    """The successful PR run whose tested tree is `sha`'s, or NoReuse naming the first unmet condition."""
    if git("rev-parse", "HEAD") != sha:
        raise NoReuse("the checkout is not the pushed commit")
    try:
        git("merge-base", "--is-ancestor", before, sha)
    except subprocess.CalledProcessError:
        raise NoReuse(f"the push does not extend {before}") from None
    touched = contract_changes(git("diff", "--name-only", before, sha).splitlines())
    if touched:
        raise NoReuse(f"the push changes how CI runs: {', '.join(touched)}")
    pulls = [pr for pr in api(f"repos/{repo}/commits/{sha}/pulls")
             if pr.get("merged_at") and pr.get("merge_commit_sha") == sha
             and pr["base"]["ref"] == "main" and pr["base"]["repo"]["full_name"] == repo]
    if len(pulls) != 1:
        raise NoReuse(f"{len(pulls)} merged pull requests have this commit as their merge commit")
    pr = pulls[0]
    number, head, branch = pr["number"], pr["head"]["sha"], pr["head"]["ref"]
    if (pr["head"].get("repo") or {}).get("full_name") != repo:
        raise NoReuse(f"PR #{number} comes from a fork")
    if api(f"repos/{repo}/git/commits/{head}")["tree"]["sha"] != git("rev-parse", "HEAD^{tree}"):
        raise NoReuse(f"the pushed tree differs from PR #{number}'s head {head}")
    listing = api(f"repos/{repo}/actions/workflows/{os.path.basename(WORKFLOW)}/runs"
                  f"?event=pull_request&head_sha={head}&per_page=100")
    if listing["total_count"] != len(listing["workflow_runs"]):
        raise NoReuse(f"PR #{number}'s head has more runs than one page lists")
    # A merged PR's runs list no pull requests once its branch is gone; the branch, head and repository bind it.
    runs = [run for run in listing["workflow_runs"]
            if run.get("path") == WORKFLOW and run.get("event") == "pull_request" and run.get("head_sha") == head
            and run.get("head_branch") == branch and (run.get("repository") or {}).get("full_name") == repo
            and (run.get("head_repository") or {}).get("full_name") == repo]
    if not runs:
        raise NoReuse(f"no run of {WORKFLOW} tested PR #{number}'s head {head}")
    run = max(runs, key=lambda item: item["id"])
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise NoReuse(f"the newest run {run['id']} of PR #{number}'s head is {run.get('conclusion') or run.get('status')}")
    jobs = api(f"repos/{repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    if jobs["total_count"] != len(jobs["jobs"]):
        raise NoReuse(f"run {run['id']} has more jobs than one page lists")
    checks = [job for job in jobs["jobs"] if job.get("name") == CHECK]
    shards = [job for job in jobs["jobs"] if job.get("name", "").startswith("shard")]
    if (len(checks) != 1 or checks[0].get("conclusion") != "success"
            or not shards or any(job.get("conclusion") != "success" for job in shards)):
        raise NoReuse(f"run {run['id']} attempt {run['run_attempt']} did not pass every shard and its {CHECK} job")
    return {"id": run["id"], "url": run["html_url"], "pr": number}


def main() -> int:
    repo, sha, before = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"], os.environ["BEFORE"]
    try:
        run = reusable_run(repo, sha, before, api=api, git=git)
    except NoReuse as reason:
        print(f"Running the full suite: {reason}.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"Running the full suite: the lookup failed ({error!r}).")
        return 0
    line = f"Tree already tested by PR #{run['pr']}'s run {run['url']}"
    print(line)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"run={run['id']}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
