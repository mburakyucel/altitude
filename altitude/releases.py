"""One operator-approved manual release, published by its current task owner.

The owner supplies a reviewed build, not commands for the daemon to execute. GitHub writes are
fixed draft/upload/publish operations. The release ledger survives uncertain HTTP outcomes;
neither retry nor recovery moves a tag, replaces an asset, or deletes a remote object.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
from urllib.parse import quote
import uuid

from . import config, installation, state as S, tasks as T


class APIError(RuntimeError):
    def __init__(self, message: str, *, refused: bool = False):
        super().__init__(message)
        self.refused = refused


def active_grant(task: dict) -> dict | None:
    grant = task.get("release_grant")
    if (not grant or task.get("state") not in ("running", "blocked", "reported")
            or grant.get("attempt") != task.get("attempt")
            or grant.get("revoked_at") or grant.get("completed_at")):
        return None
    if grant.get("expires_at") and datetime.fromisoformat(grant["expires_at"]) <= datetime.now(timezone.utc):
        return None
    return grant


def notes(version: str, changelog: str) -> str:
    match = re.search(rf"^## {re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not match or not match.group(1).strip():
        raise ValueError(f"CHANGELOG.md has no dated section '## {version} — YYYY-MM-DD' with notes")
    return match[1].strip() + "\n"


def manual_notes(version: str, changelog: str) -> str:
    return notes(version, changelog) + ("\nManual publication from an operator-approved owner build. "
                                      "Checksums verify integrity; this release has no hosted build attestation.\n")


def _git(project: str, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=config.project_path(project),
                            capture_output=True, text=True, timeout=30, env=config.subprocess_env())
    if result.returncode:
        raise ValueError("release source Git read failed")
    return result.stdout.strip()


def _repository(project: str) -> str:
    origin = _git(project, "remote", "get-url", "origin")
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?", origin)
    if not match:
        raise ValueError("release requires the registered project's GitHub origin")
    return match[1]


def _names(version: str) -> list[str]:
    archive = f"altitude-{version}.tar.gz"
    return [archive, archive + ".sha256", "install.py", "install.sh", "SHA256SUMS"]


@contextmanager
def _directory(path: Path):
    """Walk descriptors so neither directory parents nor selected assets can redirect a read."""
    path = path.absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in (".", ".."):
                raise ValueError("release paths must not contain traversal")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _files(path: Path, version: str) -> dict[str, bytes]:
    with _directory(path) as directory:
        if set(os.listdir(directory)) != set(_names(version)):
            raise ValueError("release directory must contain exactly the five build_release.py assets")
        result = {}
        for name in _names(version):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("release assets must be regular files")
                content = source.read(installation.ARCHIVE_LIMIT + 1)
            if len(content) > installation.ARCHIVE_LIMIT:
                raise ValueError("release asset exceeds the archive size limit")
            result[name] = content
        return result


def _hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}


def _verify_files(files: dict[str, bytes], repository: str, version: str, sha: str) -> None:
    archive = _names(version)[0]
    hashes = _hashes(files)
    expected = "".join(f"{hashes[name]}  {name}\n" for name in sorted((archive, "install.py", "install.sh")))
    if files["SHA256SUMS"].decode() != expected or files[archive + ".sha256"].decode().strip() != hashes[archive]:
        raise ValueError("release checksums do not match its assets")
    with tempfile.TemporaryDirectory(prefix="altitude-release-check-") as directory:
        area = Path(directory)
        bundle = area / archive
        bundle.write_bytes(files[archive])
        metadata = installation.extract(bundle, hashes[archive], area / "contents")
        if (metadata["version"], metadata["commit"], metadata.get("repository")) != (
                version, sha, f"https://github.com/{repository}"):
            raise ValueError("release archive does not match the approved repository, version and commit")
        if (area / "contents/altitude/installation.py").read_bytes() != files["install.py"]:
            raise ValueError("release installer does not match the archive")
    script = files["install.sh"].decode()
    for value in (version, repository, hashes[archive], hashes["install.py"]):
        if value not in script:
            raise ValueError("release install.sh does not identify its approved assets")


def grant(project: str, slug: str, approval: str, *, version: str, sha: str, files: str,
          reason: str, actor: str, source: str = "task", question: str | None = None,
          revision: int | None = None, expires: str | None = None, expected_attempt: int | None = None) -> dict:
    config.project(project)
    S.require_task_slug(slug)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        try:
            if actor not in ("l2", "l3", T.OPERATOR_MESSAGE_ROLE) or not reason.strip():
                raise ValueError("release grant needs the owner/coordinator/operator and a reason")
            if task["state"] not in ("running", "blocked", "reported"):
                raise ValueError("release task is not active")
            if actor == "l2" and (task["state"] != "running" or expected_attempt != task["attempt"] or source != "task"):
                raise ValueError("only the current owner records its task-chat approval")
            if not installation.VERSION.fullmatch(version) or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
                raise ValueError("supply a release version and unambiguous commit SHA")
            repository = _repository(project)
            commit = _git(project, "rev-parse", "--verify", sha + "^{commit}")
            row = next((r for r in T._decision_messages(project, slug, source) if r["id"] == approval), None)
            if not row or row.get("removed_at") or row.get("role") != T.OPERATOR_MESSAGE_ROLE or row.get("by") != T.OPERATOR_MESSAGE_ROLE:
                raise ValueError("release approval must be the operator's original message")
            scope = row["text"]
            if question is not None or revision is not None:
                if question is None or revision is None:
                    raise ValueError("contextual approval requires both question and exact revision")
                target = T._question_target(task, question, revision)
                row = T._decision_source(project, slug, target, approval, source, exact=True)
                if target["audience"] != "operator":
                    raise ValueError("release question must be addressed to the operator")
                if source == "project" and not (
                        {"id": question, "revision": revision} in row.get("question_refs", [])
                        or question in row["text"] and re.search(rf"\brevision\s+{revision}\b", row["text"], re.I)):
                    raise ValueError("project-chat approval must identify this task's exact question revision")
                scope += "\n" + target["detail"]
            versions = set(re.findall(r"(?<![\w.-])v0\.\d+\.\d+(?:-rc\.[1-9]\d*)?(?![\w.-])", scope))
            commits = re.findall(r"(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])", scope)
            if version not in versions or not any(commit.startswith(candidate) for candidate in commits):
                raise ValueError("approval or its exact referenced question must name this version and commit")
            if source == "project" and not (slug in row["text"] or question and (
                    question in row["text"] or {"id": question, "revision": revision} in row.get("question_refs", []))):
                raise ValueError("project-chat approval must identify this task or its exact question")
            previous = task.get("release_grant")
            history = S.read_events(project, slug)
            used = [e for e in history if e["kind"] in ("release-revoked", "release-completed")]
            if any(e.get("approval") == approval and e.get("source") == source for e in used):
                raise ValueError("revoked or consumed release approval cannot be reused")
            deadline = datetime.fromisoformat(expires) if expires else None
            if deadline and (deadline.tzinfo is None or deadline <= datetime.now(timezone.utc)):
                raise ValueError("release deadline must be a future timestamp with timezone")
            directory = Path(files).absolute()
            roots = [S.task_dir(project, slug).absolute(), Path(task["worktree"]).absolute()]
            if not any(directory.is_relative_to(root) and directory != root for root in roots):
                raise ValueError("build release files in a subdirectory of this task's folder or worktree")
            content = _files(directory, version)
            _verify_files(content, repository, version, commit)
            text = manual_notes(version, _git(project, "show", f"{commit}:CHANGELOG.md"))
            identity = {"repository": repository, "version": version, "sha": commit, "hashes": _hashes(content), "notes": text}
            if previous and active_grant(task):
                raise ValueError("task already has a release grant; revoke it before changing purpose")
            originals = [e for e in history if e["kind"] == "release-granted"
                         and e.get("approval") == approval and e.get("source") == source]
            for original in originals:
                if any(original.get(key) != value for key, value in identity.items()) or original.get("expires_at") != (deadline.isoformat() if deadline else None):
                    raise ValueError("replacement grant must retain the exact approved release and deadline")
            result = {**identity, "id": uuid.uuid4().hex, "approval": approval, "source": source,
                      "question": question, "revision": revision, "answer": row["text"], "approved_at": row["at"],
                      "question_refs": row.get("question_refs", []),
                      "files": str(directory), "attempt": task["attempt"], "expires_at": deadline.isoformat() if deadline else None,
                      "actor": actor, "reason": reason.strip(), "at": S.now()}
            result["publish_command"] = f"alt task publish {slug}"
            result["check_command"] = f"alt task publish {slug} --check"
            task["release_grant"] = result
            S.save_task(project, task)
            S.append_event(project, slug, "release-granted", **result)
            return result
        except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, T.TransitionError) as exc:
            S.append_event(project, slug, "release-grant-refused", actor=actor, approval=approval, error=str(exc))
            raise T.TransitionError(f"release grant refused: {exc}") from exc


def revoke(project: str, slug: str, reason: str, *, actor: str, expected_attempt: int | None = None) -> dict:
    config.project(project)
    S.require_task_slug(slug)
    with S.project_lock(project):
        task = S.load_task(project, slug)
        if (actor not in ("l2", "l3", T.OPERATOR_MESSAGE_ROLE) or not reason.strip()
                or actor == "l2" and expected_attempt != task.get("attempt")):
            raise T.TransitionError("only the owner/coordinator/operator may revoke a release grant")
        grant = task.get("release_grant")
        if not grant:
            raise T.TransitionError("task has no release grant")
        grant["revoked_at"] = S.now()
        S.save_task(project, task)
        S.append_event(project, slug, "release-revoked", actor=actor, approval=grant["approval"],
                       source=grant["source"], reason=reason)
        return grant


def _api(repository: str, path: str, *, method: str = "GET", data: dict | None = None,
         upload: Path | None = None, missing: bool = False):
    endpoint = f"repos/{repository}/{path}"
    args = ["gh", "api", "--hostname", "github.com", "--method", method]
    if upload is not None:
        endpoint = "https://uploads.github.com/" + endpoint
        args += ["-H", "Content-Type: application/octet-stream", "--input", str(upload)]
    elif data is not None:
        args += ["--input", "-"]
    result = subprocess.run([*args, endpoint], input=json.dumps(data) if data is not None else "",
                            capture_output=True, text=True, timeout=120, env=config.subprocess_env())
    if result.returncode:
        if missing and "HTTP 404" in result.stderr:
            return None
        status = re.search(r"HTTP (\d{3})", result.stderr)
        refused = bool(status and 400 <= int(status[1]) < 500)
        raise APIError(f"GitHub {method} {path.split('?')[0]} failed"
                       + ("; request refused" if refused else "; remote outcome may be uncertain"), refused=refused)
    return json.loads(result.stdout)


def _pages(repository: str, path: str, key: str | None = None):
    page = 1
    while True:
        data = _api(repository, path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        rows = data[key] if key else data
        yield from rows
        if len(rows) < 100:
            break
        page += 1


def _gate(grant: dict) -> None:
    repository, sha = grant["repository"], grant["sha"]
    comparison = _api(repository, f"compare/{sha}...main")
    if comparison.get("status") not in ("ahead", "identical") or comparison.get("merge_base_commit", {}).get("sha") != sha:
        raise ValueError("approved commit is not on GitHub main")
    path = f"actions/workflows/self-hosted-checks.yml/runs?head_sha={sha}&event=push&branch=main&status=success"
    for run in _pages(repository, path, "workflow_runs"):
        if (run.get("head_sha"), run.get("event"), run.get("head_branch"), run.get("conclusion")) != (sha, "push", "main", "success"):
            continue
        if any(job.get("name") == "check" and job.get("conclusion") == "success"
               for job in _pages(repository, f"actions/runs/{int(run['id'])}/jobs", "jobs")):
            return
    raise ValueError("approved commit has no successful main push check job")


def _tag(grant: dict) -> dict | None:
    repository = grant["repository"]
    ref = _api(repository, "git/ref/tags/" + grant["version"], missing=True)
    if ref is None:
        return None
    obj = ref["object"]
    seen = set()
    while obj["type"] == "tag" and obj["sha"] not in seen:
        seen.add(obj["sha"])
        obj = _api(repository, "git/tags/" + obj["sha"])["object"]
    if obj.get("type") != "commit" or obj.get("sha") != grant["sha"]:
        raise ValueError("existing tag does not resolve to the approved commit")
    return ref


def _remote(grant: dict) -> dict | None:
    rows = [row for row in _pages(grant["repository"], "releases") if row.get("tag_name") == grant["version"]]
    if len(rows) > 1:
        raise ValueError("multiple releases match the approved tag; reconcile before publishing")
    return rows[0] if rows else None


def _assets(grant: dict, release: dict, *, complete: bool, marker: str | None = None) -> set[str]:
    body = grant["notes"].strip()
    if release.get("draft") and marker:
        body += f"\n\n<!-- altitude-release:{marker} -->"
    if (release.get("tag_name") != grant["version"]
            or release.get("draft") and release.get("target_commitish") != grant["sha"]
            or release.get("body", "").strip() != body
            or release.get("name") != "Altitude " + grant["version"]
            or release.get("prerelease") != ("-rc." in grant["version"])):
        raise ValueError("remote release identity or notes differ from the grant")
    names = set()
    for asset in _pages(grant["repository"], f"releases/{int(release['id'])}/assets"):
        name = asset["name"]
        if (name in names or name not in grant["hashes"] or asset.get("state") != "uploaded"
                or asset.get("digest") != "sha256:" + grant["hashes"][name]):
            raise ValueError("remote asset differs or has no verified SHA-256 digest; no replacement is permitted")
        names.add(name)
    if complete and names != set(grant["hashes"]):
        raise ValueError("remote release is missing approved assets")
    return names


@contextmanager
def _publication(grant: dict):
    identity = hashlib.sha256((grant["repository"].lower() + "/" + grant["version"]).encode()).hexdigest()
    folder = config.ROOT / "releases"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / (identity + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another publish request for this release is running") from exc
        try:
            yield folder / (identity + ".json")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run(project: str, slug: str, attempt: object, *, owner, check: bool = False) -> dict:
    """Called only through altd's authenticated HTTP door with its process-origin verifier."""
    config.project(project)
    S.require_task_slug(slug)
    request = uuid.uuid4().hex
    grant = None

    def current(*, completed: bool = False) -> dict:
        task = S.load_task(project, slug)
        found = active_grant(task)
        saved = task.get("release_grant") or {}
        if (completed and saved.get("completed_at") and not saved.get("revoked_at")
                and saved.get("attempt") == task.get("attempt")):
            found = saved
        if task["state"] != "running" or str(task["attempt"]) != str(attempt) or not owner(task):
            raise PermissionError("only this task's current worker may publish its release")
        if not found or grant is not None and found["id"] != grant["id"]:
            raise PermissionError("release grant is missing, expired, revoked, completed or replaced")
        return found

    def event(kind: str, **fields):
        S.append_event(project, slug, kind, request=request, attempt=attempt,
                       grant_id=grant["id"] if grant else None, **fields)

    def finish(release: dict, *, already: bool = False) -> dict:
        result = {"status": "published", "url": f"https://github.com/{grant['repository']}/releases/tag/{grant['version']}",
                  "version": grant["version"], "sha": grant["sha"], "already_published": already}
        if check:
            event("release-checked", outcome="published")
            return result
        with S.project_lock(project):
            task = S.load_task(project, slug)
            if (task.get("release_grant") or {}).get("id") == grant["id"]:
                task["release_grant"]["completed_at"] = S.now()
                S.save_task(project, task)
            event("release-completed", approval=grant["approval"], source=grant["source"], **result)
        return result

    try:
        with S.project_lock(project):
            grant = current(completed=True)
            event("release-attempt", check=check, repository=grant["repository"], version=grant["version"], sha=grant["sha"])
        if _repository(project) != grant["repository"]:
            raise ValueError("registered repository changed since approval")
        with _publication(grant) as ledger:
            record = S.read_json(ledger, {})
            scope = {key: grant[key] for key in ("repository", "version", "sha", "hashes", "notes")}
            if record and (record.get("project") != project or record.get("slug") != slug or record.get("scope") != scope):
                raise ValueError("this release belongs to another task or approved content")
            files = _files(Path(grant["files"]), grant["version"])
            if _hashes(files) != grant["hashes"]:
                raise ValueError("release files changed after grant")
            _gate(grant)
            tag = _tag(grant)
            release = _remote(grant)
            if record.get("pending") == "create" and not record.get("release_id") and release:
                # The draft-only opaque receipt identifies our create even when its response was lost.
                # It is removed by the single publish PATCH, never exposing task or incident evidence.
                _assets(grant, release, complete=False, marker=record["marker"])
                if not release["draft"]:
                    raise ValueError("unrecorded published release cannot be adopted")
                record.update(release_id=release["id"], pending=None)
                S.write_json(ledger, record)
                event("release-reconciled", phase="create", release_id=release["id"])
            if release and release.get("draft") and release["id"] != record.get("release_id"):
                raise ValueError("existing draft is not this task's recorded draft")
            if record.get("pending") == "create" and not record.get("release_id"):
                raise ValueError("draft creation outcome is uncertain; reconcile the recorded attempt before retrying")
            if tag and not release:
                raise ValueError("existing tag may have another publisher; reconcile before publishing")
            uploaded = _assets(grant, release, complete=not release["draft"], marker=record.get("marker")) if release else set()
            if release and not release["draft"]:
                if not tag:
                    raise ValueError("published release has no approved tag")
                return finish(release, already=True)
            if grant.get("completed_at"):
                raise ValueError("completed publication is missing or no longer published; approval is consumed")
            if check:
                event("release-checked", outcome="ready")
                return {"status": "ready", "version": grant["version"], "sha": grant["sha"]}
            if not record:
                record = {"project": project, "slug": slug, "scope": scope, "approval": grant["approval"],
                          "marker": uuid.uuid4().hex, "at": S.now()}
                S.write_json(ledger, record)

            def write(phase: str, path: str, **kwargs):
                with S.project_lock(project):
                    current()
                    record["pending"] = phase
                    S.write_json(ledger, record)
                    event("release-write", phase=phase)
                    try:
                        result = _api(grant["repository"], path, **kwargs)
                    except APIError as exc:
                        if exc.refused:
                            record["pending"] = None
                            if phase == "create":
                                ledger.unlink()
                            else:
                                S.write_json(ledger, record)
                            event("release-write-refused", phase=phase)
                        raise
                    if phase == "create":
                        record["release_id"] = result["id"]
                    record["pending"] = None
                    S.write_json(ledger, record)
                    event("release-write-finished", phase=phase)
                    return result

            if release is None:
                release = write("create", "releases", method="POST", data={"tag_name": grant["version"],
                    "target_commitish": grant["sha"], "name": "Altitude " + grant["version"],
                    "body": grant["notes"].strip() + f"\n\n<!-- altitude-release:{record['marker']} -->",
                    "draft": True, "prerelease": "-rc." in grant["version"]})
            with tempfile.TemporaryDirectory(prefix="altitude-release-upload-") as directory:
                for name, content in files.items():
                    if name in uploaded:
                        continue
                    asset = Path(directory) / name
                    asset.write_bytes(content)
                    write("upload:" + name, f"releases/{int(release['id'])}/assets?name={quote(name, safe='')}",
                          method="POST", upload=asset)
            fresh = _api(grant["repository"], f"releases/{int(release['id'])}")
            _assets(grant, fresh, complete=True, marker=record["marker"])
            if not fresh["draft"]:
                raise ValueError("release was published concurrently; reconcile before reporting completion")
            _tag(grant)
            write("publish", f"releases/{int(release['id'])}", method="PATCH",
                  data={"draft": False, "body": grant["notes"],
                        "make_latest": "false" if "-rc." in grant["version"] else "true"})
            published = _api(grant["repository"], f"releases/{int(release['id'])}")
            _assets(grant, published, complete=True)
            if published["draft"] or _tag(grant) is None:
                raise ValueError("publication is not confirmed")
            record["completed_at"] = S.now()
            S.write_json(ledger, record)
            return finish(published)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        event("release-refused" if grant is None or isinstance(exc, PermissionError) else "release-failed", error=str(exc))
        raise
