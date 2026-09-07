import { useCallback, useEffect, useState } from "react";
import { Outlet, useParams } from "react-router";
import { ToastViewport } from "../data/Toast";
import { useOverview } from "../data/api";
import FirstRun from "../routes/FirstRun";
import { LAYOUT_SIZES, useViewport } from "./breakpoints";
import { Overlay } from "./Overlay";
import { PhoneHeader } from "./PhoneHeader";
import { managedProjects } from "./projects";
import { Rail } from "./Rail";
import { RestartBanner } from "./RestartBanner";
import { setSelectedProject } from "./scope";
import { TabBar } from "./TabBar";

/** Layout route: rail beside the main pane on desktop; header, content, tab bar on the phone (SPEC.md §2.2). */
export default function AppShell() {
  // Shared with every page through the ["overview"] query cache: one read, one poll.
  const overview = useOverview();
  const { phone } = useViewport();
  const params = useParams();
  const [addingFolder, setAddingFolder] = useState(false);
  const closeFirstRun = useCallback(() => setAddingFolder(false), []);

  // The scope rule: a project route selects its project (SPEC.md §2.3).
  const routeProject = params.name ?? "";
  const managedNames = managedProjects(overview.data).map((p) => p.name).join("\n");
  useEffect(() => {
    if (routeProject && managedNames.split("\n").includes(routeProject)) setSelectedProject(routeProject);
  }, [routeProject, managedNames]);

  return (
    <div className="shell" data-phone={phone || undefined} style={LAYOUT_SIZES}>
      {/* The restart banner sits above the header on every route (SPEC.md §3.13): above the phone
          header, and at the top of the main pane above a page's own header on the desktop. */}
      {phone ? (
        <>
          <RestartBanner restart={overview.data?.restart} />
          <PhoneHeader overview={overview} />
        </>
      ) : (
        <Rail overview={overview} onAddFolder={() => setAddingFolder(true)} />
      )}
      <main className="shell-main">
        {phone ? null : <RestartBanner restart={overview.data?.restart} />}
        <Outlet />
      </main>
      {phone ? <TabBar overview={overview.data} /> : null}
      {addingFolder ? (
        <Overlay label="Add a folder" side="center" onClose={closeFirstRun}>
          <FirstRun overview={overview} onStarted={closeFirstRun} />
        </Overlay>
      ) : null}
      <ToastViewport
        className={
          phone
            ? "bottom-[calc(var(--tab-bar-h)+12px)] left-1/2 -translate-x-1/2"
            : "bottom-6 left-[calc(var(--rail-w)+24px)]"
        }
      />
    </div>
  );
}
