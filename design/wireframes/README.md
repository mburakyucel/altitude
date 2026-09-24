# Altitude product design

## Compact mobile chat

[SPEC §8](SPEC.md#8-compact-mobile-chat) defines the compact phone behavior shown in the
maintained project/task and composer boards. `gen.py` generates those boards from shared sources.
Application walkthroughs in `web/e2e/mobile-chat.pw.ts`, `task-details.pw.ts` and `conversation.pw.ts`
save review captures under ignored `web/ui-artifacts/`. Native keyboard acceptance uses a real phone.
## L2 activity and steering

[Review task interaction states](TaskStates.html) and the
[interaction and engine feasibility notes](l2-progress/PROPOSAL.md). The operator approved the
preview and steering for #302 on 2026-09-09; the delivered application remains held for review.
Stop stays directly accessible beside the composer and in Live session on phone and desktop;
task metadata and Reject remain in the compact phone details sheet.

## Approved image-input interaction

[Images in project and task chats](IMAGE_INPUT.md) describes the interaction approved on 2026-09-10.
The shared composer spec and `web/e2e/image-input.pw.ts` maintain its states and phone/desktop
walkthrough. Review captures stay outside Git. The compact mobile layout is defined above.

## Approved conversation-first design

[Review the Needs you / L2 conversation design](CONVERSATION_FIRST.md), with
[phone and desktop boards](index.html). The operator settled the single/grouped-question UX on
2026-09-08. The boards show its distinct decision states; shared input and recovery examples live
in one appendix per viewport.
`conversation_first.py`, called by `gen.py`, generates these boards and styles. The conversation-first
boards define decision behavior; the other boards below retain the broader shell and session layout studies.
Current decision interactions are the conversation-first boards and SPEC §3.8–3.10.
The 2026-09-10 Pacific Work separation is folded into Project and MobileWork: every current task
appears once as a status row, while questions and quick answers stay in Needs you and chat.
Only global Needs you has a numeric attention badge; project state dots remain.

This folder is the design record Altitude's own L3 and L2 read: static boards, the generator that
writes them, and [`SPEC.md`](SPEC.md), which states every interaction, state, and rule the boards
illustrate. **The boards and spec are the source of truth together**, for visual design and rules;
accepted departures are folded into both. Nothing here depends on a hosted tool: the files open
from disk, from `serve.sh`, or from the Altitude UI's Design link.

Every design iteration follows the [standing design tenet and review expectations](SPEC.md#11-standing-design-tenet).
Keep this tree focused on current approved design. Fold accepted changes into the spec and useful
boards; remove obsolete studies and links. Review captures, comparisons and per-PR galleries stay
outside Git under the [project UI rule](../../AGENTS.md#ui).

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
| Settings | `Settings.html`, `MobileSettings.html`, `VoiceSettings.html`, `MobileVoiceSettings.html`, `SettingsStates.html` | Compact machine overview and nested voice choices, entry points, endpoint form and save states. Spec §3.15; application walkthroughs use ignored captures. |
| Project | `Project.html`, `MobileProject.html` | The rail (global Needs you badge, projects with state dots, unmanaged folders, engine readout, Monitor, operator). L3 conversation with task cards and folded system lines beside compact Current rows and recent Done history. Phone combines identity/status in one header and stacks the composer field over its control row; engine selection and metadata open in details. Chat, Work, Needs you, Monitor return after keyboard dismissal. |
| Project switcher | `MobileSwitcher.html` | The sheet the header name opens: projects with state dots, unmanaged folders, Add a folder. Desktop has no switcher; the rail is always visible. |
| Project work | `MobileWork.html` | The work panel as the phone's Work tab: all current tasks, including questions awaiting an answer, as status rows opening chat; folded recent Done history. |
| Needs you and L2 decisions | `ConversationFirst*.html`, `MobileConversationFirst*.html` | Six examples: questions upfront, immediate single choices, grouped picks, follow-up, partial/irrelevant closure, and resumed work. Shared recovery/input appendix. |
| Task page | `Task.html`, `MobileTask.html`, `MobileTaskLive.html` | L2 conversation, replacing public activity preview and delivery receipts, with direct Stop beside the composer and in Live session. Phone details hold metadata and Reject; concise status stays above Conversation and Live session tabs. The transcript uses recorded boundaries and tool output hints. |
| Task overlay and states | `TaskOverlay.html`, `TaskStates.html` | The live panel overlays the main pane below 1280px. Running, stopping, stopped/correction/Continue, unconfirmed Stop, quiet/unavailable activity, delivery receipts, resume, Reject confirmation, blocked, finished and existing transcript/message states. |
| Report | `Report.html`, `MobileReport.html` | Full report and digest as plain sections, with a back link to the task. |
| Monitor | `Monitor.html`, `MobileMonitor.html`, `MonitorStates.html` | Configured engine seats, reserve lines, reading ages, stale chips, routing and Sessions (N). Loading, error, no reading, one engine, and empty states. The route boards also show the restart banner above the header/content. |
| Restart banner states | `RestartStates.html` | Pending at the quiet point, waiting with a named reason, under way with the button gone, and absent after the new process answers. |
| First run | `FirstRun.html` | No managed project yet: the folders Altitude found, pick one, Start L3. |
| Composer states | `ComposerStates.html` | Phone-width examples of idle, typing, listening (Cancel, Stop, arrow), transcribing, landed, busy, denied, unavailable, failed and send refusal. Every state has one send control: the accent arrow. Routine hints have no phone row; relevant voice and failure explanations remain visible. No transcript box or Undo chip appears. |
| Voice input states | `VoiceStates.html` | Listening with recognized words (browser backend), listening and transcribing with a server backend, landed, unavailable and failed, each at desktop and phone width. Desktop keeps Cancel, waveform, timer, Stop and the arrow together at the right of the row; the phone row fills its width. |
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
The reusable `web/e2e/conversation-first-wireframes.pw.ts` and application walkthroughs write named
phone/desktop captures to ignored `web/ui-artifacts/`. Keep evidence needed for review accessible and
link it from the PR; see [development](../../docs/DEVELOPMENT.md#browser-walkthroughs).

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
