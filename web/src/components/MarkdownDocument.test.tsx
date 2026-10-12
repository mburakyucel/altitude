import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import MarkdownDocument from "./MarkdownDocument";
import { shownReference } from "../shown";

const saved = (path: string, name: string) => ({ name: path.split("/").pop()!, path, url: `/api/shown/atlas/readme/m1/files/${name}`, width: 640, height: 360, embedded: true });

describe("shown image references", () => {
  it.each([
    ["images/a.png", "docs/guide.md", "docs/images/a.png"],
    ["../images/a.png", "docs/guide.md", "images/a.png"],
    ["/images/a.png", "docs/guide.md", "images/a.png"],
    ["./a%20b.png?raw=1#top", "README.md", "a b.png"],
    ["a%zz.png", "README.md", "a%zz.png"],
    ["https://example.org/a.png", "README.md", null],
    ["data:image/png;base64,AAAA", "README.md", null],
    ["//example.org/a.png", "README.md", null],
    ["../a.png", "README.md", undefined],
    ["", "README.md", undefined],
    ["a\\b.png", "README.md", undefined],
  ])("resolves %s from %s as publication does", (source, document, expected) => {
    expect(shownReference(source, document)).toBe(expected);
  });
});

describe("markdown documents", () => {
  it("renders GitHub markdown with headings, task lists, tables and code without actions", () => {
    const text = "# Setup\n\n## Commands\n\n- [x] Build\n- [ ] Ship\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n```run\nmake check\n```";
    const { container } = render(<MarkdownDocument text={text} />);
    expect(screen.getByRole("heading", { name: "Setup", level: 1 })).toHaveAttribute("id", "user-content-setup");
    expect(screen.getByRole("heading", { name: "Commands", level: 2 })).toBeVisible();
    expect(screen.getAllByRole("checkbox").map((box) => [(box as HTMLInputElement).checked, (box as HTMLInputElement).disabled])).toEqual([[true, true], [false, true]]);
    expect(container.querySelector(".md-table table")).not.toBeNull();
    expect(container.querySelector("pre")?.textContent).toBe("make check\n");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("executes nothing and loads only saved images", () => {
    const text = [
      "<script>window.ran = 1</script>",
      "<img src=\"x.png\" onerror=\"window.ran = 2\">",
      "<p style=\"background:url(https://example.org/t.png)\" onclick=\"window.ran = 3\">Styled</p>",
      "[Run](javascript:window.ran=4) and [Sibling](other.md) and [Site](https://example.org/page) and [Mail](mailto:team@example.org)",
      "<picture><source srcset=\"https://example.org/s.png\"><img src=\"https://example.org/p.png\" alt=\"Picture\"></picture>",
      "![Inline](data:image/png;base64,AAAA) ![Relative](//example.org/r.png) ![Badge](https://example.org/badge.svg)",
      "[![Build](https://example.org/build.svg)](https://example.org/ci)",
      "<iframe src=\"https://example.org/frame\"></iframe><form action=\"https://example.org/post\"><input name=\"q\"><button>Go</button></form>",
      "![Missing](images/missing.png) ![Shown](images/shown.png)",
    ].join("\n\n");
    const { container } = render(<MarkdownDocument text={text} path="README.md" images={[saved("images/shown.png", "abc.png")]} onOpenImage={vi.fn()} />);
    expect(container.querySelector("script, iframe, form, input, picture, source, [style], [onclick], [onerror], [srcset]")).toBeNull();
    expect([...container.querySelectorAll("img")].map((img) => img.getAttribute("src"))).toEqual(["/api/shown/atlas/readme/m1/files/abc.png"]);
    expect(screen.getByText("Run").closest("a")).toBeNull();
    expect(screen.getByText("Sibling")).toHaveAttribute("title", "other.md");
    expect(screen.getByRole("link", { name: "Site" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Mail" })).toHaveAttribute("href", "mailto:team@example.org");
    expect(screen.getByRole("link", { name: "Badge" })).toHaveAttribute("href", "https://example.org/badge.svg");
    expect(screen.getByRole("link", { name: "Build" })).toHaveAttribute("href", "https://example.org/ci");
    expect(screen.getByRole("link", { name: "Picture" })).toHaveAttribute("href", "https://example.org/p.png");
    expect([...container.querySelectorAll(".md-not-shown")].map((box) => box.textContent)).toEqual(["Image not shownx.png", "Image not shown", "Image not shown", "Image not shownimages/missing.png"]);
    expect(screen.getByRole("button", { name: "Open image shown.png" })).toBeVisible();
    expect((window as { ran?: number }).ran).toBeUndefined();
  });

  it("opens a copied image's gallery unless it sits inside a link, and scrolls to a heading", async () => {
    const open = vi.fn();
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const text = "[Install](#install)\n\n![Shot](shot.png)\n\n[![Linked](shot.png)](https://example.org)\n\n## Install";
    render(<MarkdownDocument text={text} path="docs/a.md" images={[saved("docs/shot.png", "abc.png")]} onOpenImage={open} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Open image shot.png" }));
    expect(open).toHaveBeenCalledWith(0);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    await user.click(screen.getByRole("link", { name: "Install" }));
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.contexts[0]).toBe(screen.getByRole("heading", { name: "Install" }));
  });

  it("names link targets for a coordinator's message", () => {
    render(<MarkdownDocument text="See [the release](https://example.org/r/12)." showLinkTargets />);
    expect(screen.getByRole("link")).toHaveTextContent("the release (https://example.org/r/12)");
  });
});
