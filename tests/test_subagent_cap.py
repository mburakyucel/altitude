"""I-005: the cap hook bills one launch per subagent call — command position, not substring, and one bill per tool call."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="altitude-cap-")
os.environ["ALTITUDE_HOME"] = _TMP
HOOK = Path(__file__).resolve().parent.parent / "hooks" / "subagent_cap.py"

LAUNCH_CLAUDE = "claude " + "-p"          # split so this file's own text is not a launch line
LAUNCH_CODEX = "codex " + "exec"


class CapHook(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="altitude-cap-", dir=_TMP)  # fresh counts file per test

    def run_hook(self, payload, env=None):
        e = dict(os.environ, ALTITUDE_HOME=self.home)
        e.pop("ALTITUDE_SESSION_KEY", None)
        e.update(env or {})
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              text=True, capture_output=True, env=e)

    def bash(self, command, **kw):
        p = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": command}}
        p.update(kw)
        return self.run_hook(p)

    def counted(self, sid="s1", key=None):
        f = Path(self.home) / "monitor" / f"counts-{key or sid}.json"
        return json.loads(f.read_text())["subagent_launches"] if f.exists() else 0

    # --- dedupe -----------------------------------------------------------------
    def test_same_tool_use_id_is_billed_once(self):
        for _ in range(3):
            r = self.bash(LAUNCH_CLAUDE + " 'do a thing'", tool_use_id="toolu_abc")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.counted(), 1)

    def test_distinct_tool_use_ids_are_billed_separately(self):
        self.bash(LAUNCH_CLAUDE + " 'a'", tool_use_id="toolu_1")
        self.bash(LAUNCH_CLAUDE + " 'b'", tool_use_id="toolu_2")
        self.assertEqual(self.counted(), 2)

    def test_dedupe_without_tool_use_id_falls_back_to_input_hash(self):
        self.bash(LAUNCH_CODEX + " 'review the diff'")
        self.bash(LAUNCH_CODEX + " 'review the diff'")
        self.assertEqual(self.counted(), 1)

    # --- position, not substring ------------------------------------------------
    def test_heredoc_body_quoting_a_launch_is_not_billed(self):
        body = "\n".join(["the hook matched " + LAUNCH_CLAUDE + " inside data",
                          "and also " + LAUNCH_CODEX + " here", "EOF"])
        self.bash("gh pr create --title x --body-file - <<'EOF'\n" + body)
        self.assertEqual(self.counted(), 0)

    def test_unquoted_heredoc_body_is_not_billed(self):
        body = "\n".join(["report: " + LAUNCH_CODEX + " ran", LAUNCH_CLAUDE + " ran too", "EOF"])
        self.bash("cat > report.md <<EOF\n" + body + "\necho done")
        self.assertEqual(self.counted(), 0)

    def test_dash_heredoc_with_indented_terminator_is_not_billed(self):
        self.bash("cat <<-EOF\n\t" + LAUNCH_CLAUDE + " nope\n\tEOF")
        self.assertEqual(self.counted(), 0)

    def test_unterminated_heredoc_strips_to_end(self):
        self.bash("cat <<EOF\n" + LAUNCH_CODEX + " still data")
        self.assertEqual(self.counted(), 0)

    def test_launch_inside_a_quoted_argument_is_not_billed(self):
        self.bash("echo 'remember: " + LAUNCH_CLAUDE + " starts a worker'")
        self.assertEqual(self.counted(), 0)
        self.bash('grep -n "' + LAUNCH_CODEX + '" hooks/subagent_cap.py')
        self.assertEqual(self.counted(), 0)

    def test_ordinary_command_is_not_billed(self):
        self.bash("ls -la")
        self.assertEqual(self.counted(), 0)

    # --- genuine launches -------------------------------------------------------
    def test_plain_launch_is_billed(self):
        self.bash(LAUNCH_CLAUDE + " 'build the thing'")
        self.assertEqual(self.counted(), 1)

    def test_launch_after_and_and_is_billed(self):
        self.bash("git fetch && " + LAUNCH_CODEX + " 'review'")
        self.assertEqual(self.counted(), 1)

    def test_launch_with_var_prefix_is_billed(self):
        self.bash("FOO=bar BAZ=1 " + LAUNCH_CLAUDE + " 'go'")
        self.assertEqual(self.counted(), 1)

    def test_launch_after_pipe_and_in_subshell_is_billed(self):
        self.bash("cat brief.md | " + LAUNCH_CLAUDE + " 'go'")
        self.assertEqual(self.counted(), 1)
        self.bash("(cd /tmp && " + LAUNCH_CODEX + " 'go')")
        self.assertEqual(self.counted(), 2)

    def test_bg_and_print_forms_are_billed(self):
        self.bash("claude " + "--bg 'go'", tool_use_id="t1")
        self.bash("claude " + "--print 'go'", tool_use_id="t2")
        self.assertEqual(self.counted(), 2)

    # --- other tools ------------------------------------------------------------
    def test_agent_tool_bills_one(self):
        r = self.run_hook({"session_id": "s1", "tool_name": "Agent",
                           "tool_input": {"prompt": "go"}, "tool_use_id": "toolu_agent"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.counted(), 1)

    # --- I-004 capability probes -------------------------------------------------
    def test_capability_probe_is_not_billed(self):
        self.bash(LAUNCH_CODEX + " --help")
        self.bash(LAUNCH_CLAUDE + " --version")
        self.bash("claude " + "--bg -h")
        self.assertEqual(self.counted(), 0)

    def test_probe_flag_elsewhere_does_not_exempt_a_real_launch(self):
        self.bash("make --help > /dev/null; " + LAUNCH_CLAUDE + " 'go'")
        self.assertEqual(self.counted(), 1)

    # --- cap --------------------------------------------------------------------
    def test_over_cap_exits_2_with_the_envelope_message(self):
        mon = Path(self.home) / "monitor"
        mon.mkdir(parents=True, exist_ok=True)
        (mon / "envelope-k1.json").write_text(json.dumps({"subagent_launches": 2}))
        env = {"ALTITUDE_SESSION_KEY": "k1"}
        for i in range(2):
            r = self.run_hook({"session_id": f"s{i + 1}", "tool_name": "Agent", "tool_input": {"prompt": str(i)},
                               "tool_use_id": f"ok{i}"}, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_hook({"session_id": "s2", "tool_name": "Agent", "tool_input": {"prompt": "over"},
                           "tool_use_id": "over"}, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("altitude: envelope reached (2/2 subagent launches)", r.stderr)
        again = self.run_hook({"session_id": "s2", "tool_name": "Agent", "tool_input": {"prompt": "over"},
                               "tool_use_id": "over"}, env=env)  # dedupe never weakens blocking
        self.assertEqual(again.returncode, 2)
        self.assertIn("altitude: envelope reached", again.stderr)

    def test_resume_continues_the_dispatch_keyed_count(self):
        env = {"ALTITUDE_SESSION_KEY": "demo--task-1"}
        for sid, tool_use_id in (("old-session", "t1"), ("new-session", "t2")):
            r = self.run_hook({"session_id": sid, "tool_name": "Agent", "tool_input": {"prompt": sid},
                               "tool_use_id": tool_use_id}, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.counted(key="demo--task-1"), 2)

    def test_dedupe_survives_a_resume(self):
        env = {"ALTITUDE_SESSION_KEY": "demo--task-1"}
        for sid in ("old-session", "new-session"):
            r = self.run_hook({"session_id": sid, "tool_name": "Agent", "tool_input": {"prompt": "same"},
                               "tool_use_id": "same-tool-use"}, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.counted(key="demo--task-1"), 1)

    def test_without_dispatch_key_counts_remain_per_session(self):
        for sid in ("session-a", "session-b"):
            r = self.run_hook({"session_id": sid, "tool_name": "Agent", "tool_input": {"prompt": sid},
                               "tool_use_id": f"tool-{sid}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.counted(sid=sid), 1)

    def test_other_hooks_keys_survive(self):
        mon = Path(self.home) / "monitor"
        mon.mkdir(parents=True, exist_ok=True)
        (mon / "counts-s1.json").write_text(json.dumps({"edits": 4, "files": ["a.py"]}))
        self.bash(LAUNCH_CLAUDE + " 'go'", tool_use_id="t9")
        c = json.loads((mon / "counts-s1.json").read_text())
        self.assertEqual(c["edits"], 4)
        self.assertEqual(c["files"], ["a.py"])
        self.assertEqual(c["subagent_launches"], 1)

    def test_seen_list_is_bounded(self):
        for i in range(210):
            self.run_hook({"session_id": "s1", "tool_name": "Agent", "tool_input": {"prompt": str(i)},
                           "tool_use_id": f"t{i}"})
        c = json.loads((Path(self.home) / "monitor" / "counts-s1.json").read_text())
        self.assertLessEqual(len(c["seen"]), 200)

    # --- review of the I-005 fix: data-blanking must never hide executable text -----
    # Each of these executes in bash and was billed before the fix; a parser that reads them
    # as data under-bills, which is a weaker guardrail than the bug being fixed (R-006).
    def test_command_substitution_inside_double_quotes_is_billed(self):
        self.bash('x="$(' + LAUNCH_CLAUDE + " 'go')\"")
        self.assertEqual(self.counted(), 1)

    def test_backtick_substitution_is_billed(self):
        self.bash("x=`" + LAUNCH_CLAUDE + ' "go"`')
        self.assertEqual(self.counted(), 1)

    def test_here_string_does_not_swallow_a_following_launch(self):
        self.bash('cat <<< "ignore"\n' + LAUNCH_CLAUDE + ' "do something"')
        self.assertEqual(self.counted(), 1)

    def test_quoted_heredoc_marker_does_not_swallow_a_following_launch(self):
        self.bash('echo "use <<EOF here"\n' + LAUNCH_CLAUDE + ' "go"')
        self.assertEqual(self.counted(), 1)

    def test_apostrophe_in_a_heredoc_body_does_not_hide_a_following_launch(self):
        self.bash("cat <<EOF\nit's fine\nEOF\n" + LAUNCH_CLAUDE + " 'go'")
        self.assertEqual(self.counted(), 1)

    def test_backslash_escaped_verb_is_billed(self):
        self.bash("cd /tmp && \\" + LAUNCH_CLAUDE + " 'go'")
        self.assertEqual(self.counted(), 1)

    def test_substitution_output_used_as_data_is_not_billed(self):
        self.bash('echo "$(date) ' + LAUNCH_CLAUDE + ' x"')
        self.assertEqual(self.counted(), 0)

    def test_probe_inside_a_substitution_is_not_billed(self):
        self.bash("x=$(" + LAUNCH_CODEX + " --help)")
        self.assertEqual(self.counted(), 0)


if __name__ == "__main__":
    unittest.main()
