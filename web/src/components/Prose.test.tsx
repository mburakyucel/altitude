import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InlineProse, Prose, ProseAt, ProseRepository, ProseScope, QuestionProse, lastParagraph } from "./Prose";
import { clockText, exactTime } from "../data/observed";

const repository = "https://github.com/example/project";
function prose(text: string, repo: string | null = repository) {
  return render(<ProseRepository value={repo}><Prose text={text} /></ProseRepository>);
}
function destinations() {
  return screen.queryAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")]);
}

describe("local file references", () => {
  const path = "/home/operator/.altitude/atlas/tasks/setup/commands.md";
  const route = (target: string, project = "atlas") => `/projects/${project}/file?path=${encodeURIComponent(target)}`;
  it.each(["reply", "compact"])("links file URIs, absolute paths and labeled references in %s", (surface) => {
    const text = `Read file://${path}, then (${path}). [Commands](${path}) and [With spaces](</srv/task/my commands.md>).`;
    render(<ProseScope project="atlas" repository={repository}>{surface === "reply" ? <Prose text={text} /> : <InlineProse text={text} />}</ProseScope>);
    expect(destinations()).toEqual([[`file://${path}`, route(`file://${path}`)], [path, route(path)], ["Commands", route(path)], ["With spaces", route("/srv/task/my commands.md")]]);
    for (const link of screen.getAllByRole("link")) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      expect(link.getAttribute("title")).toBeTruthy();
    }
    expect(screen.getByText(/, then/)).toBeVisible();
  });
  it("preserves web links and code without linking embedded paths or image resources", () => {
    const text = `https://example.org/doc.md [Web](https://example.org/doc.md) \`${path}\`\n\n~~~sh\ncat ${path}\n~~~\n\nword/path ../relative //host/share 1 / 2 ![photo](https://example.org/photo.png) [Bad](javascript:alert)`;
    const { container } = render(<ProseScope project="atlas" repository={repository}><Prose text={text} /></ProseScope>);
    expect(destinations()).toEqual([["https://example.org/doc.md", "https://example.org/doc.md"], ["Web", "https://example.org/doc.md"]]);
    expect(container.querySelector("img, iframe, a a")).toBeNull();
  });
  it("keeps the exact URI encoding and sends foreign-host URIs to the bounded reader for refusal", () => {
    render(<ProseScope project="atlas"><Prose text="file:///srv/task/a%20b.md file://another-machine/etc/notes.md" /></ProseScope>);
    expect(destinations()).toEqual([["file:///srv/task/a%20b.md", route("file:///srv/task/a%20b.md")], ["file://another-machine/etc/notes.md", route("file://another-machine/etc/notes.md")]]);
  });
  it("keeps balanced filename parentheses while excluding surrounding sentence punctuation", () => {
    const target = "/srv/task/setup(v2).md";
    render(<ProseScope project="atlas"><Prose text={`Read ${target}, then (file://${target}).`} /></ProseScope>);
    expect(destinations()).toEqual([[target, route(target)], [`file://${target}`, route(`file://${target}`)]]);
  });
  it("uses the displayed project's identity independently of repository metadata", () => {
    const view = render(<Prose text={path} />);
    expect(destinations()).toEqual([]);
    view.rerender(<ProseScope project="atlas"><Prose text={path} /></ProseScope>);
    expect(destinations()).toEqual([[path, route(path)]]);
    view.rerender(<ProseScope project="second"><Prose text={path} /></ProseScope>);
    expect(destinations()).toEqual([[path, route(path, "second")]]);
  });
});

describe("project-aware GitHub references", () => {
  it.each(["reply", "compact", "folded"])("preserves same-number upstream and local issues in %s prose", (surface) => {
    const text = "Local issue #42; upstream example/altitude#42 and https://github.com/example/altitude/issues/42";
    const content = surface === "reply" ? <Prose text={text} /> :
      <InlineProse text={surface === "folded" ? lastParagraph(`Earlier context.\n\n${text}`) : text} />;
    render(<ProseRepository value={repository}>{content}</ProseRepository>);
    expect(destinations()).toEqual([
      ["issue #42", `${repository}/issues/42`],
      ["example/altitude#42", "https://github.com/example/altitude/issues/42"],
      ["https://github.com/example/altitude/issues/42", "https://github.com/example/altitude/issues/42"],
    ]);
  });

  it("links explicit, bare, cross-repository, bold, list and heading references", () => {
    prose("PR #250, pull request #251, ISSUE #247; (#248).\n\n**PR #252**\n\n- other/repo#12\n- PR someone/.github#13\n\n### #249", `${repository}/`);
    expect(destinations()).toEqual([
      ["PR #250", `${repository}/pull/250`], ["pull request #251", `${repository}/pull/251`],
      ["ISSUE #247", `${repository}/issues/247`], ["#248", `${repository}/issues/248`],
      ["PR #252", `${repository}/pull/252`], ["other/repo#12", "https://github.com/other/repo/issues/12"],
      ["PR someone/.github#13", "https://github.com/someone/.github/pull/13"], ["#249", `${repository}/issues/249`],
    ]);
    for (const link of screen.getAllByRole("link")) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("preserves Markdown links, bare URLs and their reference-looking labels or fragments", () => {
    const { container } = prose("[PR #250](https://example.org/review#247) https://example.org/other/repo#12 `PR #251` **[issue #252](https://example.org/252)** [#253](javascript:alert) [#254](/local)");
    expect(destinations()).toEqual([
      ["PR #250", "https://example.org/review#247"], ["https://example.org/other/repo#12", "https://example.org/other/repo#12"],
      ["issue #252", "https://example.org/252"],
    ]);
    expect(container.querySelector("a a")).toBeNull();
  });

  it("leaves single/multiple-backtick, multiline, fenced and unfinished fenced code untouched", () => {
    prose("`#1` ``PR #2 ` issue #3``\n\n`multiline\n#4`\n\n````md\n#5\n```\n#6\n````\n\n~~~\nother/repo#7\n~~~\n\n```\n#8");
    expect(destinations()).toEqual([]);
    expect(screen.getByText("multiline #4")).toBeInTheDocument();
  });

  it.each([null, "", "javascript:alert(1)", "https://github.com.evil.test/a/b", "https://github.com/a/b/../c", "https://github.com/a/b?x=1", "https://user@github.com/a/b", "https://github.com/a/.."])("does not invent a project target from %s", (repo) => {
    prose("PR #250, issue #247, #248, other/repo#12", repo);
    expect(destinations()).toEqual([["other/repo#12", "https://github.com/other/repo/issues/12"]]);
  });

  it("ignores unrelated numbers and malformed/embedded references", () => {
    prose("250 2026 1.5 #0 #012 #123abc #123_abc word#123 /path#123 ##123 \\#123 #12.5 x/../repo#12 @owner/repo#12 ../repo#12");
    expect(destinations()).toEqual([]);
  });

  it("updates saved prose when repository context arrives or changes without retaining the old target", () => {
    const tree = (repo: string | null) => <ProseRepository value={repo}><Prose text="PR #250" /></ProseRepository>;
    const view = render(tree(null));
    expect(destinations()).toEqual([]);
    view.rerender(tree(repository));
    expect(destinations()).toEqual([["PR #250", `${repository}/pull/250`]]);
    view.rerender(tree("https://github.com/second/project"));
    expect(destinations()).toEqual([["PR #250", "https://github.com/second/project/pull/250"]]);
    view.rerender(tree(null));
    expect(destinations()).toEqual([]);
  });

  it("uses the same rules in compact decision and report text", () => {
    render(<ProseRepository value={repository}><InlineProse text={"PR #250 `#251`\n~~~\n#252\n~~~\n````\n```\n#253"} /></ProseRepository>);
    expect(destinations()).toEqual([["PR #250", `${repository}/pull/250`]]);
  });

  it("omits code from folded system summaries using the same fence boundaries", () => {
    expect(lastParagraph("Merged PR #250.\n\n````\n```\nissue #247\n````")).toBe("Merged PR #250.");
    expect(lastParagraph("Merged PR #250.\n\n~~~\nissue #247")).toBe("Merged PR #250.");
  });
});

describe("question prose", () => {
  it("keeps a single line as one compact paragraph with inline code and links", () => {
    const { container } = render(<ProseRepository value={repository}><QuestionProse className="decision-question" text="Merge `main` after PR #9?" /></ProseRepository>);
    expect(container.innerHTML).toBe(`<p class="decision-question">Merge <code>main</code> after <a class="prose-link" href="${repository}/pull/9" target="_blank" rel="noopener noreferrer">PR #9</a>?</p>`);
  });
  it("renders line breaks as paragraphs, lists and code blocks inside the card's element", () => {
    const text = "Install it?\n\n- keeps `alt` on PATH\n- one copy\n\n```sh\nsudo make install\n```\nSee [docs](https://example.org/install).";
    const { container } = render(<ProseRepository value={repository}><QuestionProse className="decision-why" text={text} /></ProseRepository>);
    const root = container.firstElementChild!;
    expect([root.tagName, root.className]).toEqual(["DIV", "decision-why"]);
    expect([...root.querySelectorAll(".session-prose > *")].map((node) => node.tagName)).toEqual(["P", "UL", "DIV", "P"]);
    expect(root.querySelector("ul")?.textContent).toBe("keeps alt on PATHone copy");
    expect(root.querySelector("pre.session-code")?.textContent).toBe("sudo make install");
    expect(destinations()).toEqual([["docs", "https://example.org/install"]]);
  });
});

describe("written times", () => {
  it("shows UTC times in the reader's zone and leaves code, links and zoneless clocks alone", () => {
    const text = "Restart pending since 23:58Z; checks at 2026-10-11T01:30:00+00:00 and 07:20–07:45 UTC (PR #12). " +
      "`2026-10-10T23:58:42+00:00` and a bare 23:58 stay.\n\n~~~\nat 23:58Z\n~~~";
    const { container } = render(<ProseAt value="2026-10-11T01:55:00+00:00"><Prose text={text} /></ProseAt>);
    const times = [...container.querySelectorAll("time.prose-time")];
    const local = (iso: string) => new Date(iso).toTimeString().slice(0, 5);
    expect(times.map((t) => t.getAttribute("datetime"))).toEqual([local("2026-10-10T23:58:00Z"), "2026-10-11T01:30:00.000Z", local("2026-10-11T07:20:00Z")]);
    expect(times[0]).toHaveTextContent(clockText("2026-10-10T23:58:00Z"));
    expect(times[1]).toHaveAttribute("title", exactTime("2026-10-11T01:30:00Z"));
    expect(times[2]).toHaveTextContent(`${clockText("2026-10-11T07:20:00Z")}–${clockText("2026-10-11T07:45:00Z")}`);
    expect(container.querySelector("p")!.textContent).not.toMatch(/\dZ\b|UTC|T01:30/);
    expect(screen.getByText("2026-10-10T23:58:42+00:00").tagName).toBe("CODE");
    expect(container.textContent).toContain("a bare 23:58 stay");
    expect(container.querySelector("pre, .code-block")?.textContent).toContain("at 23:58Z");
    expect(destinations()).toEqual([]);
  });

  it("leaves a relative path that contains a timestamp as written", () => {
    const { container } = render(<Prose text="Saved to logs/2026-10-10T23:58Z.txt at 23:58Z." />);
    expect(container.textContent).toContain("logs/2026-10-10T23:58Z.txt");
    expect(container.querySelectorAll("time.prose-time")).toHaveLength(1);
  });

  it("converts times in compact prose too", () => {
    const { container } = render(<InlineProse text="since 2026-10-10T23:58Z" />);
    expect(container.querySelector("time.prose-time")).toHaveAttribute("datetime", "2026-10-10T23:58:00.000Z");
  });
});
