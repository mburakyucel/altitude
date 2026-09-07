# Altitude product design

This folder is the design record Altitude's own L3 and L2 read: static boards, the generator that
writes them, and [`SPEC.md`](SPEC.md), which states every interaction, state, and rule the boards
illustrate. **The boards and spec are the source of truth together**, for visual design and rules;
accepted departures are folded into both. Nothing here depends on a hosted tool: the files open
from disk, from `serve.sh`, or from the Altitude UI's Design link.

Burak approved this direction on 2026-09-05. It replaces the 2026-09-03 set (#166), which polished
the console-shaped app (Inbox, Projects, Chat, Monitor as four routes). The redesign starts from the
product instead: the rail lists projects, each project is one conversation with its L3, and the work
that conversation creates sits beside it. One cross-project surface remains, **Needs you**.
Implementation proceeds in the slices of `SPEC.md` §7, one task each, and starts only with the
operator's explicit go.

## What each board shows

Desktop boards are 1440×900, phone boards an exact portrait 390×844 iPhone screen, sheets 1200 wide.
The live-panel overlay example is 1100×900. Every board opens in light; add `?dark` for dark.
Phone headers, composers and tab bars stay docked while content scrolls inside the shell.

| Board | Files | What it shows |
| --- | --- | --- |
| Project | `Project.html`, `MobileProject.html` | The rail (Needs you, projects with state dot and count, unmanaged folders, engine readout, Monitor, operator). The project's conversation with L3: operator bubbles, L3 prose, an inline task card for a task the turn created, one folded system line, the composer with engine pin and microphone. The work panel: Needs you cards, Active rows, Done this week folded. On the phone the project name in the header is the switcher, and the tab bar is Chat, Work, Needs you, Monitor. |
| Project switcher | `MobileSwitcher.html` | The sheet the header name opens: projects with dot and count, unmanaged folders, Add a folder. Desktop has no switcher; the rail is always visible. |
| Project work | `MobileWork.html` | The work panel as the phone's Work tab: same sections and cards as the desktop panel. |
| Needs you | `NeedsYou.html`, `MobileNeedsYou.html` | Every decision waiting on the operator, grouped by project, each card with the asker's recommendation, two option buttons, and More context. The one cross-project route. |
| Decision page | `Decision.html`, `MobileDecision.html` | What More context opens: the question, the options with an optional note, why the asker recommends one, where the question came from (a short timeline), evidence chips into the task, the live session, and the record, and a follow-up composer addressed to whoever asked. The work panel keeps the card selected. |
| Task page | `Task.html`, `MobileTask.html`, `MobileTaskLive.html` | L2 conversation, labelled L3 prose, attempt/age/context metadata and PR/check chips. Phone actions sit at the end of the state line, above Conversation and Live session tabs. The transcript uses recorded boundaries and tool output hints. |
| Task overlay and states | `TaskOverlay.html`, `TaskStates.html` | The live panel overlays the main pane below 1280px. Inline Stop/Reject confirmations, L3 block, held resume, connecting, streaming, paused, unavailable, finished/empty conversation, and message failure. |
| Report | `Report.html`, `MobileReport.html` | Full report and digest as plain sections, with a back link to the task. |
| Monitor | `Monitor.html`, `MobileMonitor.html`, `MonitorStates.html` | Configured engine seats, reserve lines, reading ages, stale chips, routing and Sessions (N). Loading, error, no reading, one engine, and empty states. The route boards also show the restart banner above the header/content. |
| Restart banner states | `RestartStates.html` | Pending at the quiet point, waiting with a named reason, under way with the button gone, and absent after the new process answers. |
| First run | `FirstRun.html` | No managed project yet: the folders Altitude found, pick one, Start L3. |
| Composer states | `ComposerStates.html` | Phone-width examples of idle, typing, listening (Cancel, Stop, arrow), transcribing, landed, busy, denied, unavailable, failed and send refusal. Every state has one send control: the accent arrow. Busy and voice hints sit below the field; no transcript box or Undo chip appears. |
| Decision card states | `DecisionStates.html` | Waiting, follow-up sent, answer arrived, asked by an L2, deciding, decided, failed. |
| System turns in chat | `SystemTurnStates.html` | A landed report, a fault, a restart, or an FYI is one folded line in the conversation: one turn, several grouped, expanded, in progress, fault, FYI. |
| Conversation and report states | `ConversationStates.html` | L3 never started, empty conversation, loading and cached-error rows, report loading, empty and error. |
| Project lifecycle states | `ProjectLifecycleStates.html` | The removal confirmation that detaches L3, cancellation, pending, denial/error, navigation, and attachment again with saved history. The same controls fit phone and desktop. |

The restart banner follows `SPEC.md` §3.13: running workers can remain while dispatch, L3 and
verification reach the quiet point. Monitor readings and session snapshots show their age.

## View

```
google-chrome design/wireframes/index.html      # every board on one canvas; see VIEWER.md
design/wireframes/serve.sh                      # the same over HTTP, for a phone on the network
```

The Altitude UI links to the same viewer from the project page whenever the checkout has these
boards. Any single board opens on its own as a plain file.

## Render

```
design/wireframes/shots.sh
```

Renders every board in both themes into `design/wireframes/shots/` (gitignored) with the Chrome on
this machine (`CHROME=` overrides). Each board's size is read from the board itself.

## Change

The boards, `wireframes.css`, and `boards.js` are written by `gen.py`; edit the generator, run
`python3 design/wireframes/gen.py`, and commit the output with it. A new board is one `board(...)`
call and one row in the generator's `ROUTES` table. `wireframes.css` imports the build's
`web/design/tokens.css`, which carries the redesign's tokens since slice 1; `SPEC.md` §6 records
where they departed from the earlier set.
A change in behaviour updates `SPEC.md` and the matching boards together.

## Content

The exchanges, tasks, quota readings, and project names on the boards are realistic, not recorded.
Engine names appear as the configured engines would; the operator's name is the configured one.
