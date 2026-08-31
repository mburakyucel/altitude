"""The command guard inspects executed shell text, not quoted prose."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class TestGuardExecutedText(unittest.TestCase):
    def run_guard(self, command):
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "guard.py")],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True,
            text=True,
        )

    def assert_blocked(self, command):
        result = self.run_guard(command)
        self.assertEqual(result.returncode, 2, (command, result.stderr))
        return result

    def assert_allowed(self, command):
        result = self.run_guard(command)
        self.assertEqual(result.returncode, 0, (command, result.stderr))

    def test_blocks_executed_service_restart(self):
        self.assert_blocked("systemctl --user restart altitude")
        self.assert_blocked('systemctl --user restart "altitude"')

    def test_ignores_heredoc_body(self):
        self.assert_allowed(
            "cat > report.md <<'EOF'\n"
            "after merge this needs systemctl --user restart altitude\n"
            "EOF\n"
        )

    def test_blocks_shell_heredoc_body(self):
        for command in (
            "bash <<'EOF'\nsystemctl --user restart altitude\nEOF\n",
            "bash -s <<EOF\nsystemctl --user restart altitude\nEOF\n",
            "sh <<EOF\nsystemctl --user restart altitude\nEOF\n",
            'bash -c "bash <<EOF\nsystemctl --user restart altitude\nEOF\n"',
        ):
            self.assert_blocked(command)

    def test_handles_multiple_and_tab_stripped_heredocs(self):
        self.assert_allowed(
            'cat <<ONE <<-"TWO"\n'
            "systemctl --user restart altitude\n"
            "ONE\n"
            "\tsystemctl --user restart altitude\n"
            "\tTWO\n"
            "systemctl --user status altitude\n"
        )

    def test_unterminated_heredoc_drops_to_end(self):
        self.assert_allowed(
            "cat <<EOF\n"
            "systemctl --user restart altitude\n"
        )

    def test_heredoc_like_text_does_not_hide_later_commands(self):
        for command in (
            "echo ok # <<EOF\nsystemctl --user restart altitude",
            'cat <<< "ordinary input"\nsystemctl --user restart altitude',
            "echo $((1 << 2))\nsystemctl --user restart altitude",
            'alt fyi "quoted line\n<<EOF\nstill quoted"\nsystemctl --user restart altitude',
        ):
            self.assert_blocked(command)

    def test_ignores_heredoc_inside_quoted_shell_code(self):
        self.assert_allowed(
            'bash -c "cat <<\'EOF\'\n'
            "systemctl --user restart altitude\n"
            'EOF\n"'
        )

    def test_ignores_quoted_prose_arguments(self):
        self.assert_allowed('alt fyi "after merge this needs systemctl --user restart altitude"')
        self.assert_allowed('git commit -m "note: systemctl --user restart altitude after merge"')

    def test_blocks_quoted_command_words(self):
        for command in (
            '"systemctl" --user restart altitude',
            "'ufw' allow 8890",
            '"rm" -rf ~/.altitude',
            '"kill" -9 altd',
            'X=1 "systemctl" --user restart altitude',
        ):
            self.assert_blocked(command)

    def test_blocks_double_quoted_command_substitutions(self):
        for command in (
            'echo "$(systemctl --user restart altitude)"',
            'echo "`systemctl --user restart altitude`"',
            'echo "$(sudo ufw allow 1234)"',
        ):
            self.assert_blocked(command)
        self.assert_allowed("echo '$(systemctl --user restart altitude)'")

    def test_blocks_quoted_shell_code_recursively(self):
        for command in (
            'bash -c "systemctl --user restart altitude"',
            'bash -lc "systemctl --user restart altitude"',
            'bash -c "bash -c \'systemctl --user restart altitude\'"',
            'eval "systemctl --user restart altitude"',
        ):
            self.assert_blocked(command)

    def test_blocks_shell_code_behind_wrappers(self):
        for command in (
            'timeout 5 bash -c "systemctl --user restart altitude"',
            'time bash -c "systemctl --user restart altitude"',
            'xargs bash -c "systemctl --user restart altitude"',
            'nice bash -c "systemctl --user restart altitude"',
            'ionice bash -c "systemctl --user restart altitude"',
            'stdbuf -oL bash -c "systemctl --user restart altitude"',
            'setsid bash -c "systemctl --user restart altitude"',
            'script -q -c "systemctl --user restart altitude" /dev/null',
            'sudo -u burak bash -c "systemctl --user restart altitude"',
            'bash <<< "systemctl --user restart altitude"',
        ):
            self.assert_blocked(command)

    def test_other_rules_keep_executed_operands(self):
        for command in (
            "sudo ufw allow 8890",
            "rm -rf ~/.altitude",
            'rm -rf "$HOME/.altitude"',
            "git push --force origin main",
        ):
            self.assert_blocked(command)
        self.assert_allowed("git push -u origin HEAD")

    def test_blocks_git_hook_and_verification_bypasses(self):
        for command in (
            "git -c core.hooksPath=/tmp push origin HEAD:main",
            "git -c=core.hooksPath=/tmp status",
            "git --config-env=core.hooksPath=HOOKS push origin HEAD",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath "
            "GIT_CONFIG_VALUE_0=/tmp git push origin HEAD",
            "git push --no-verify origin HEAD:main",
            "git commit --no-verify -m bypass",
            "git commit -n -m bypass",
            "git commit -qn -m bypass",
            "git merge --no-verify topic",
            "git -c alias.ship='!git push origin HEAD:main' ship",
            "export GIT_CONFIG_GLOBAL=/tmp/bypass; git push origin HEAD",
            "GIT_CONFIG_GLOBAL=/tmp/bypass\ngit push origin HEAD",
            "alias ship='git -c core.hooksPath=/tmp push origin HEAD:main'; ship",
            "ship(){ git -c core.hooksPath=/tmp push origin HEAD:main; }; ship",
            'script -q -c "git push origin HEAD:main" /dev/null',
            'bash <<< "git push origin HEAD:main"',
            "env -S 'git push origin HEAD:main'",
            "echo `git push origin HEAD:main`",
            "cat <(git push origin HEAD:main)",
            "cat >(git push origin HEAD:main)",
            "if true; then git push origin HEAD:main; fi",
            "case x in x) git push origin HEAD:main;; esac",
            "for x in one; do git push origin HEAD:main; done",
            "! git push origin HEAD:main",
            "ship() ( git push origin HEAD:main ); ship",
            'cmd="git push origin HEAD:main"; $cmd',
            'args="push origin HEAD:main"; git $args',
            'gitcmd="git -c core.hooksPath=/tmp push origin HEAD:main"; $gitcmd',
        ):
            self.assert_blocked(command)

    def test_blocks_git_config_and_metadata_mutations(self):
        for command in (
            "git config core.hooksPath /tmp",
            'git config core.hooksPath ""',
            "git config core.hooksPath=",
            "git config --local core.hooksPath=/tmp",
            "git config --unset core.hooksPath",
            "git config unset core.hooksPath",
            "git config --add alias.ship '!git push origin HEAD:main'",
            "git config include.path /tmp/bypass.gitconfig",
            "printf x > .git/config",
            'printf x >> "../../.git/config"',
            "tee .git/hooks/pre-push",
            "sed -i s/main/topic/ .git/HEAD",
            "perl -pi -e s/x/y/ .git/config",
            "cp refs .git/packed-refs",
            "printf x > $(git rev-parse --git-common-dir)/config",
            "rm .git/config",
            "unlink .git/hooks/pre-push",
            "rm \"$(git rev-parse --git-common-dir)/config\"",
            "python3 -c \"open('.git/config','w').write('x')\"",
            "truncate hooks/guard.py",
            'target=.git/config; printf x > "$target"',
            "shred .git/config",
            "rm -rf hooks",
            "cp /tmp/pre-push hooks/",
            "install -t hooks /tmp/pre-push",
            "mv /tmp/pre-push hooks/",
            "rsync /tmp/pre-push hooks/",
            'target=; printf x > ${target:-.git/config}',
        ):
            self.assert_blocked(command)

    def test_blocks_every_explicit_protected_push_shape(self):
        for command in (
            "git push origin HEAD:main",
            "git push origin HEAD:refs/heads/main",
            "git push origin :main",
            "git push origin --delete master",
            "git push https://github.com/example/repo.git +HEAD:main",
            "git push origin +HEAD:task/feature",
            "git push -fu origin task/feature",
            "git -C /tmp/repo push origin topic:refs/heads/master",
            "git push origin HEAD:ma\\in",
            "git push origin HEAD:$'main'",
            "dst=main; git push origin HEAD:$dst",
            "git push --all origin",
            "git push --mirror origin",
            "git send-pack origin HEAD:refs/heads/main",
            "git update-ref refs/heads/main HEAD",
            "git update-ref --stdin <<'EOF'\nupdate refs/heads/main HEAD\nEOF\n",
            'ref=; git update-ref ${ref:-refs/heads/main} HEAD',
            'name=ref; ref=refs/heads/main; git update-ref ${!name} HEAD',
            "gh api repos/example/repo/git/refs/heads/main -X PATCH -f sha=deadbeef",
            "gh api repos/example/repo/git/refs/heads/main -XDELETE",
            "gh api repos/example/repo/merges -X POST -f base=main -f head=topic",
            "base=main; gh api repos/example/repo/merges -f base=$base -f head=topic",
        ):
            self.assert_blocked(command)

    def test_protected_git_rules_apply_through_wrappers_and_compounds(self):
        for command in (
            "env git -c core.hooksPath=/tmp push origin HEAD:main",
            "command git push origin HEAD:main",
            "timeout 5 git push origin HEAD:main",
            'bash -c "git -c core.hooksPath=/tmp push origin HEAD:main"',
            "git status; git push origin HEAD:main",
            "git status\ngit push origin HEAD:main",
        ):
            self.assert_blocked(command)

    def test_allows_topic_pushes_and_read_only_git_config_queries(self):
        for command in (
            "git push -u origin task/feature",
            "git push origin HEAD:refs/heads/task/feature",
            "git -C /tmp/repo push origin HEAD:refs/heads/task/feature",
            "git push origin HEAD:refs/heads/main-feature",
            "git push origin refs/heads/main:refs/heads/task/feature",
            "git push -ofoo origin HEAD:refs/heads/task/feature",
            "git config --get core.hooksPath",
            "git config --local --get core.hooksPath",
            "git config --show-origin --get-regexp '^core\\.'",
            "git config --list --show-origin",
            "git config --get-color core.hooksPath default",
            "git config core.hooksPath",
            "git -c color.ui=false status",
            "sed -n 1p .git/config",
            "cp .git/config /tmp/backup",
            "cp hooks/pre-push /tmp/backup",
            "python3 -c \"print(open('.git/config').read())\"",
            "export GIT_CONFIG_GLOBAL=/tmp; unset GIT_CONFIG_GLOBAL; git status",
            'git commit -m "document git push origin HEAD:main without running it"',
            "echo git push origin HEAD:main",
            "echo ok # git push origin HEAD:main",
            "echo '$(git push origin HEAD:main)'",
            "echo '`git push origin HEAD:main`'",
        ):
            self.assert_allowed(command)

    def test_guard_closes_real_pre_push_bypasses_before_remote_ref_moves(self):
        if not shutil.which("git"):
            self.skipTest("git not available")
        with tempfile.TemporaryDirectory(prefix="alt-guard-push-") as tmp:
            root = Path(tmp)
            remote = root / "origin.git"
            repo = root / "repo"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            for key, value in (
                ("user.email", "test@example.invalid"),
                ("user.name", "Guard Test"),
                ("commit.gpgsign", "false"),
            ):
                subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
            (repo / "README.md").write_text("initial\n")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
            initial = subprocess.check_output(
                ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"], text=True,
            ).strip()
            subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "topic"], check=True)
            (repo / "topic.txt").write_text("topic\n")
            subprocess.run(["git", "-C", str(repo), "add", "topic.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "topic"], check=True)
            topic = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            subprocess.run(
                ["git", "-C", str(repo), "config", "core.hooksPath", str(ROOT / "hooks")], check=True,
            )

            bypasses = (
                ("git push --no-verify origin topic:main", ["push", "--no-verify", "origin", "topic:main"]),
                ("git -c core.hooksPath=/tmp push origin topic:main", ["-c", "core.hooksPath=/tmp", "push", "origin", "topic:main"]),
            )
            for command, args in bypasses:
                moved = subprocess.run(
                    ["git", "-C", str(repo), *args], capture_output=True, text=True,
                )
                self.assertEqual(moved.returncode, 0, moved.stderr)
                remote_head = subprocess.check_output(
                    ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"], text=True,
                ).strip()
                self.assertEqual(remote_head, topic)
                subprocess.run(
                    ["git", "--git-dir", str(remote), "update-ref", "refs/heads/main", initial], check=True,
                )
                self.assert_blocked(command)
                unchanged = subprocess.check_output(
                    ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"], text=True,
                ).strip()
                self.assertEqual(unchanged, initial)

    def test_port_rule(self):
        self.assert_blocked("ALTITUDE_PORT=8890 bin/alt serve")
        self.assert_blocked('ALTITUDE_PORT="8890" bin/alt serve')
        self.assert_allowed("ALTITUDE_TIMERS=0 ALTITUDE_PORT=8931 bin/alt serve")
        self.assert_allowed('alt fyi "ALTITUDE_PORT=8890 is reserved"')

    def test_unterminated_quote_fails_closed(self):
        self.assert_blocked('alt fyi "unfinished: systemctl --user restart altitude')

    def test_refusal_reports_matched_fragment(self):
        result = self.assert_blocked(
            "echo " + ("ordinary-text-" * 20) + "; systemctl --user restart altitude"
        )
        self.assertIn("Matched: systemctl --user restart altitude", result.stderr)


if __name__ == "__main__":
    unittest.main()
