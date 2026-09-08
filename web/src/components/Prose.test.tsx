import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InlineProse, Prose, ProseRepository, lastParagraph } from "./Prose";

const repository = "https://github.com/example/project";
function prose(text: string, repo: string | null = repository) {
  return render(<ProseRepository value={repo}><Prose text={text} /></ProseRepository>);
}
function destinations() {
  return screen.queryAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")]);
}

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
