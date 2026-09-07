import { Fragment } from "react";
import type { ReactNode } from "react";

const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s<>)]+)/g;

/** Inline code, bold, markdown links, and bare URLs; everything else is text. */
export function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (link) {
      return (
        <a key={index} href={link[2]} target="_blank" rel="noreferrer">
          {link[1]}
        </a>
      );
    }
    if (/^https?:\/\//.test(part)) {
      return (
        <a key={index} href={part} target="_blank" rel="noreferrer">
          {part}
        </a>
      );
    }
    return part;
  });
}

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

/**
 * Markdown-lite prose for a reply (SPEC.md §3.3): paragraphs split on blank lines with line breaks
 * kept, bulleted and numbered lists, fenced code blocks, inline code, bold, and links. A heading line
 * reads as a plain paragraph: replies carry no headings and no tables, so neither gets a shape here.
 */
export function Prose({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let code: string[] | null = null;
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length > 0) {
      blocks.push(
        <p key={blocks.length}>
          {para.map((line, index) => (
            <Fragment key={index}>
              {index > 0 ? "\n" : null}
              {inline(line)}
            </Fragment>
          ))}
        </p>,
      );
    }
    para = [];
  };
  const flushList = () => {
    if (list) {
      const Tag = list.ordered ? "ol" : "ul";
      blocks.push(
        <Tag key={blocks.length}>
          {list.items.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </Tag>,
      );
    }
    list = null;
  };
  const flush = () => {
    flushPara();
    flushList();
  };

  for (const line of text.split("\n")) {
    if (line.trimStart().startsWith("```")) {
      if (code) {
        blocks.push(
          <pre key={blocks.length} className="session-code">
            {code.join("\n")}
          </pre>,
        );
        code = null;
      } else {
        flush();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    if (line.trim() === "") {
      flush();
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) {
        flush();
        list = { ordered, items: [] };
      } else flushPara();
      list.items.push((bullet ?? numbered)?.[1] ?? "");
      continue;
    }
    if (list && /^\s{2,}\S/.test(line)) {
      // A wrapped list item continues on an indented line.
      list.items[list.items.length - 1] += ` ${line.trim()}`;
      continue;
    }
    flushList();
    const heading = HEADING.exec(line);
    para.push(heading?.[1] ?? line);
  }
  if (code) {
    blocks.push(
      <pre key={blocks.length} className="session-code">
        {(code as string[]).join("\n")}
      </pre>,
    );
  }
  flush();
  return <div className="session-prose">{blocks}</div>;
}

/** The last paragraph of a reply, as one line: what a folded system line shows (SPEC.md §4.1). */
export function lastParagraph(text: string): string {
  const paragraphs = text
    .replace(/```[\s\S]*?```/g, "")
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  return paragraphs[paragraphs.length - 1] ?? "";
}
