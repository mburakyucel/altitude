import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Outlet, useMatch, useParams } from "react-router";
import { ToastViewport } from "../data/Toast";
import { useChangeStream, useOverview } from "../data/api";
import FirstRun from "../routes/FirstRun";
import { LAYOUT_SIZES, useViewport } from "./breakpoints";
import { Overlay } from "./Overlay";
import { PhoneHeader } from "./PhoneHeader";
import { managedProjects } from "./projects";
import { Rail } from "./Rail";
import { RestartBanner } from "./RestartBanner";
import { setSelectedProject, useSelectedProject } from "./scope";
import { TabBar } from "./TabBar";

/** Layout route: rail beside the main pane on desktop; header, content, tab bar on the phone (SPEC.md §2.2). */
export default function AppShell() {
  // Shared with every page through the ["overview"] query cache: one read, one poll.
  const overview = useOverview();
  useChangeStream();
  const { phone } = useViewport();
  const params = useParams();
  const [addingFolder, setAddingFolder] = useState(false);
  const closeFirstRun = useCallback(() => setAddingFolder(false), []);
  const shell = useRef<HTMLDivElement>(null);
  const projectPage = useMatch("/projects/:name");
  const taskPage = useMatch("/projects/:name/tasks/:slug");
  const taskLive = useMatch("/projects/:name/tasks/:slug/live");

  // The phone keyboard shrinks the visual viewport independently of 100dvh on iOS.
  useEffect(() => {
    const viewport = window.visualViewport;
    const node = shell.current;
    if (!phone || !viewport || !node) return;
    let fullHeight = Math.max(window.innerHeight, viewport.height);
    let width = window.innerWidth;
    const resize = () => {
      if (viewport.scale !== 1) return; // Keep native pinch zoom.
      if (width !== window.innerWidth) {
        width = window.innerWidth;
        fullHeight = window.innerHeight;
      }
      fullHeight = Math.max(fullHeight, window.innerHeight, viewport.height);
      const editable = document.activeElement?.matches('textarea, input:not([type]), input[type="text"], input[type="search"], input[type="email"], input[type="url"], input[type="tel"], input[type="password"], input[type="number"], [contenteditable="true"]');
      // Toolbar motion and hardware-keyboard focus leave navigation in place. Once detected,
      // follow dismissal even if focus moves to a composer control or into a details sheet.
      node.toggleAttribute("data-keyboard", Boolean((editable || node.hasAttribute("data-keyboard")) && fullHeight - viewport.height > Math.max(120, fullHeight * 0.2)));
      node.style.setProperty("--viewport-height", `${viewport.height}px`);
      node.style.setProperty("--viewport-top", `${viewport.offsetTop}px`);
    };
    resize();
    viewport.addEventListener("resize", resize);
    viewport.addEventListener("scroll", resize);
    document.addEventListener("focusin", resize);
    window.addEventListener("resize", resize);
    return () => {
      viewport.removeEventListener("resize", resize);
      viewport.removeEventListener("scroll", resize);
      document.removeEventListener("focusin", resize);
      window.removeEventListener("resize", resize);
      node.removeAttribute("data-keyboard");
      node.style.removeProperty("--viewport-height");
      node.style.removeProperty("--viewport-top");
    };
  }, [phone]);

  // The scope rule: a project route selects its project (SPEC.md §2.3).
  const routeProject = params.name ?? "";
  const selected = useSelectedProject();
  const projects = managedProjects(overview.data);
  const managedNames = projects.map((p) => p.name).join("\n");
  const missingProject = overview.isSuccess && routeProject && !projects.some((row) => row.name === routeProject);
  useEffect(() => {
    if (!overview.isSuccess) return;
    const names = managedNames.split("\n").filter(Boolean);
    if (routeProject && names.includes(routeProject)) setSelectedProject(routeProject);
    else if (selected && !names.includes(selected)) setSelectedProject(names[0] ?? null);
  }, [routeProject, managedNames, selected, overview.isSuccess]);

  return (
    <div className="shell" ref={shell} data-phone={phone || undefined} style={LAYOUT_SIZES}>
      {/* The restart banner sits above the header on every route (SPEC.md §3.13): above the phone
          header, and at the top of the main pane above a page's own header on the desktop. */}
      {phone ? (
        <>
          <RestartBanner restart={overview.data?.restart} />
          {(!projectPage && !taskPage && !taskLive) || missingProject ? <PhoneHeader overview={overview} /> : null}
        </>
      ) : (
        <Rail overview={overview} onAddFolder={() => setAddingFolder(true)} />
      )}
      <main className="shell-main">
        {phone ? null : <RestartBanner restart={overview.data?.restart} />}
        {missingProject ? (
          <div className="page first-run-page">
            {projects.length ? <>
              <h1 className="text-card-title font-semibold">Project not managed</h1>
              <p className="text-muted">{routeProject} is not managed by Altitude. Select a project or add its folder again.</p>
              <Link className="link" to="/projects">Open projects</Link>
            </> : <FirstRun overview={overview} />}
          </div>
        ) : <Outlet />}
      </main>
      {phone ? <TabBar overview={overview.data} stale={overview.isError} /> : null}
      {addingFolder ? (
        <Overlay label="Add a folder" side="center" onClose={closeFirstRun}>
          <FirstRun overview={overview} onStarted={closeFirstRun} />
        </Overlay>
      ) : null}
      <ToastViewport
        className={
          phone
            ? "phone-toast bottom-[calc(var(--tab-bar-h)+12px)] left-1/2 -translate-x-1/2"
            : "bottom-6 left-[calc(var(--rail-w)+24px)]"
        }
      />
    </div>
  );
}
