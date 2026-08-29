import os, sys, tempfile, json
os.environ["ALTITUDE_HOME"] = tempfile.mkdtemp(prefix="alt-test-")
sys.path.insert(0, ".")
from altitude import config, state as S, tasks as T
config.ensure_root(); P = config.load_projects(); P["demo"] = {"path": os.environ["ALTITUDE_HOME"], "stacks": ["python"]}; config.save_projects(P)
t = T.new("demo", "Add beta stage with alarm rollback", "L", "Add a beta pipeline stage…")
assert t["state"] == "requested" and t["envelope"]["subagent_launches"] == 20
T.propose("demo", t["slug"], "# Proposal\n…", {"estimate": {"turns": 90}}, question="Ship beta stage with CloudWatch alarm rollback (~$3/mo)?", options=["Approve: alarm rollback", "Approve: manual rollback", "Revise", "Park"])
try: T.approve("demo", t["slug"], 0, actor="l3"); raise SystemExit("agent approval should fail")
except T.TransitionError: pass
assert len(T.decisions("demo")) == 1
T.approve("demo", t["slug"], 0)
T.brief("demo", t["slug"], "# Brief\n…")
T.dispatch("demo", t["slug"], dispatch_id=f"{t['slug']}-1", session_id="sid", agent_id="aid", worktree="/wt", branch="b")
assert S.load_task("demo", t["slug"])["attempt"] == 1
T.block("demo", t["slug"], "envelope reached"); assert T.decisions("demo")[0]["kind"] == "blocked"
T.resume("demo", t["slug"])
T.report("demo", t["slug"], {"verdict": "ok", "prs": [140]})
T.fyi("demo", t["slug"], "landed")
T.done("demo", t["slug"], digest="Done.")
assert S.task_dir("demo", t["slug"]).parent.name == "archive"
assert len(S.read_events("demo", t["slug"])) >= 9
t2 = T.new("demo", "Add beta stage with alarm rollback", "S", "again"); assert t2["slug"].endswith("-2")
T.auto_approve("demo", t2["slug"], "docs-only")
assert "Recently finished" in open(config.project_dir("demo") / "STATE.md").read()
print("inbox", len(T.inbox("demo")), "OK")
