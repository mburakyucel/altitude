name: Tidy worktrees and branches
days: Mon
time: 09:00
tell: found
summary: Clears worktrees and branches nothing needs, with Undo
may: worktrees

Tidy the worktrees and local branches that finished work left behind. Go through the list of this project's worktrees
and branches in your prompt and ask Altitude to set aside each worktree, and delete each local branch, that nothing
needs any more, one request at a time. Altitude checks each request itself and keeps anything with work in it. Leave
alone anything that looks like work someone may come back to, even when Altitude would allow it. Report "Found" when
you changed something or found something the operator should know, naming what you left alone and why; if there was
nothing to tidy, say so in one line.
