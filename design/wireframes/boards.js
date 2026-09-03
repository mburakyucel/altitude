/* boards.js — the list of boards the viewer (index.html) lays out on its canvas.
 *
 * A board task adds its route here: one entry per route, in the order of the table in README.md,
 * with the desktop file and the iPhone file that sit beside this script. The viewer reads this file
 * through a <script> tag rather than fetching it, because Chrome blocks fetch() on file:// URLs and
 * the viewer has to open from disk with nothing installed.
 *
 * A listed file that is missing renders as a visible warning tile, never as a blank one, so a stale
 * entry here is obvious the moment the viewer opens.
 */
window.WIREFRAME_BOARDS = [
  { label: 'Inbox',                    desktop: 'Inbox.html',            mobile: 'MobileInbox.html' },
  { label: 'Projects',                 desktop: 'Projects.html',         mobile: 'MobileProjects.html' },
  { label: 'Chat',                     desktop: 'Chat.html',             mobile: 'MobileChat.html' },
  { label: 'Project page',             desktop: 'Project.html',          mobile: 'MobileProject.html' },
  { label: 'Task page, Conversation',  desktop: 'TaskConversation.html', mobile: 'MobileTaskConversation.html' },
  { label: 'Task page, Live session',  desktop: 'TaskLive.html',         mobile: 'MobileTaskLive.html' },
  { label: 'Monitor',                  desktop: 'Monitor.html',          mobile: 'MobileMonitor.html' },
  { label: 'Restart pending',          desktop: 'RestartPending.html',   mobile: 'MobileRestartPending.html' },
];

/* The native size each board renders at, matching shots.sh. A route may override either size with
 * its own `desktopSize` / `mobileSize` ({ w, h }) entry above. */
window.WIREFRAME_SIZES = {
  desktop: { w: 1440, h: 900, name: 'Desktop' },
  mobile:  { w: 390,  h: 960, name: 'iPhone' },
};
