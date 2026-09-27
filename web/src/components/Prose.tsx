import { createContext, Fragment, useContext } from "react";
import type { ReactNode } from "react";
import { CodeBlock } from "./CodeBlock";

export const ProseRepository = createContext<string | null | undefined>(null);
export const ProseProject = createContext<string | undefined>(undefined);
export function ProseScope({ project, repository, children }: { project: string; repository?: string | null; children: ReactNode }) {
  return <ProseProject value={project}><ProseRepository value={repository}>{children}</ProseRepository></ProseProject>;
}
const REPO = "[a-z\\d](?:[a-z\\d-]*[a-z\\d])?/(?=[a-z\\d_.-]*[a-z\\d_])[a-z\\d_.-]+";
const REPOSITORY = new RegExp(`^https://github\\.com/(${REPO})/?$`, "i");
const REFERENCE = new RegExp(`(?<![\\w/#@\\\\.-])(?:(PR|pull request|issue)\\s+)?(${REPO})?#([1-9]\\d*)(?![\\w/#]|[.,]\\d)`, "gi");
const INLINE = /(`+)([\s\S]*?)\1(?!`)|\*\*[^*]+\*\*|!?\[[^\]]+\]\((?:<[^>\n]+>|[^\s)]+)\)|https?:\/\/[^\s<>)]+|\bfile:\/\/[^\s<>`"'\]}]+|(?<![\w/:\\.-])\/(?!\/)[^\s<>`"'[\]{}]+/g;

/** Share fence boundaries across full prose, compact mirrors, and folded summaries. */
function codeBlocks(text: string): { text: string; code: boolean; info: string }[] {
  const blocks: { text: string; code: boolean; info: string }[] = [];
  let lines: string[] = [];
  let fence = "";
  let info = "";
  const flush = () => {
    blocks.push({ text: lines.join("\n"), code: Boolean(fence), info });
    lines = [];
  };
  for (const line of text.split("\n")) {
    const marker = /^\s*(`{3,}|~{3,})(.*)$/.exec(line);
    const delimiter = marker?.[1] ?? "";
    if (marker && (!fence || (delimiter[0] === fence[0] && delimiter.length >= fence.length && !marker[2]?.trim()))) {
      flush();
      info = fence ? "" : marker[2]!;
      fence = fence ? "" : delimiter;
    } else lines.push(line);
  }
  flush();
  return blocks;
}

/** Only plain text reaches reference matching; links and code are consumed first. */
function references(text: string, repository?: string | null): ReactNode[] {
  const nodes: ReactNode[] = [];
  let end = 0;
  for (const match of text.matchAll(REFERENCE)) {
    const repo = match[2] || REPOSITORY.exec(repository ?? "")?.[1];
    nodes.push(text.slice(end, match.index));
    const route = /^(pr|pull request)$/i.test(match[1] ?? "") ? "pull" : "issues";
    nodes.push(repo ? (
      <a className="prose-link" key={match.index} href={`https://github.com/${repo}/${route}/${match[3]}`} target="_blank" rel="noopener noreferrer">
        {match[0]}
      </a>
    ) : match[0]);
    end = match.index + match[0].length;
  }
  nodes.push(text.slice(end));
  return nodes;
}

export function InlineProse({ text }: { text: string }) {
  const repository = useContext(ProseRepository);
  const project = useContext(ProseProject);
  return <>{codeBlocks(text).map((block, index) => (
    <Fragment key={index}>
      {index > 0 ? "\n" : null}
      {block.code ? <code>{block.text}</code> : inline(block.text, repository, project)}
    </Fragment>
  ))}</>;
}

/** Inline code, bold, existing links, and GitHub references in plain text. */
export function inline(text: string, repository?: string | null, project?: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let end = 0;
  for (const match of text.matchAll(INLINE)) {
    nodes.push(<Fragment key={`text-${match.index}`}>{references(text.slice(end, match.index), repository)}</Fragment>);
    const part = match[0];
    const index = match.index;
    end = index + part.length;
    if (match[1]) {
      nodes.push(<code key={index}>{match[2]}</code>);
      continue;
    }
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      nodes.push(<strong key={index}>{inline(part.slice(2, -2), repository, project)}</strong>);
      continue;
    }
    const fileLink = /^\[([^\]]+)\]\(<?((?:file:\/\/|\/)[\s\S]*?)>?\)$/.exec(part);
    let fileTarget = fileLink?.[2] ?? (/^(file:\/\/|\/(?!\/))/.test(part) ? part.replace(/[.,;:!?]+$/, "") : null);
    if (fileTarget && !fileLink) {
      let closing = (fileTarget.match(/\)/g)?.length ?? 0) - (fileTarget.match(/\(/g)?.length ?? 0);
      while (closing-- > 0 && fileTarget.endsWith(")")) fileTarget = fileTarget.slice(0, -1);
    }
    if (project && fileTarget) {
      nodes.push(<a className="prose-link" key={index} href={`/projects/${encodeURIComponent(project)}/file?path=${encodeURIComponent(fileTarget)}`} title={fileTarget} target="_blank" rel="noopener noreferrer">
        {fileLink?.[1] ?? fileTarget}
      </a>);
      if (!fileLink) nodes.push(part.slice(fileTarget.length));
      continue;
    }
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (link) {
      nodes.push(
        <a className="prose-link" key={index} href={link[2]} target="_blank" rel="noopener noreferrer">
          {link[1]}
        </a>
      );
      continue;
    }
    if (/^https?:\/\//.test(part)) {
      nodes.push(
        <a className="prose-link" key={index} href={part} target="_blank" rel="noopener noreferrer">
          {part}
        </a>
      );
      continue;
    }
    nodes.push(part);
  }
  nodes.push(<Fragment key="tail">{references(text.slice(end), repository)}</Fragment>);
  return nodes;
}

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

/**
 * Markdown-lite prose for a reply (SPEC.md §3.3): paragraphs split on blank lines with line breaks
 * kept, bulleted and numbered lists, fenced code blocks (outside a document with Copy, and `run` blocks
 * as commands), inline code, bold, and links. A heading line reads as a plain paragraph: replies carry
 * no headings and no tables, so neither gets a shape here.
 */
export function Prose({ text, document = false }: { text: string; document?: boolean }) {
  const repository = useContext(ProseRepository);
  const project = useContext(ProseProject);
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length > 0) {
      blocks.push(
        <p key={blocks.length}>
          {inline(para.join("\n"), repository, project)}
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
            <li key={index}>{inline(item, repository, project)}</li>
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

  for (const block of codeBlocks(text)) {
    if (block.code) {
      flush();
      // A document (the file reader) stays read-only text: its code has no Copy and no terminal action.
      blocks.push(document ? <pre key={blocks.length} className="session-code">{block.text}</pre> : <CodeBlock key={blocks.length} text={block.text} info={block.info} />);
      continue;
    }
    for (const line of block.text.split("\n")) {
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
      if (document && heading) {
        flushPara();
        const Tag = `h${Math.min(6, /^#+/.exec(line.trimStart())![0].length + 1)}` as "h2" | "h3" | "h4" | "h5" | "h6";
        blocks.push(<Tag key={blocks.length}>{inline(heading[1]!, repository, project)}</Tag>);
        continue;
      }
      para.push(heading?.[1] ?? line);
    }
  }
  flush();
  return <div className="session-prose">{blocks}</div>;
}

/** A question or review card (SPEC.md §3.8): one line stays a compact paragraph; line breaks bring paragraphs, lists and code blocks. */
export function QuestionProse({ text, className }: { text: string; className: string }) {
  return text.includes("\n") ? <div className={className}><Prose text={text} /></div> : <p className={className}><InlineProse text={text} /></p>;
}

/** The last paragraph of a reply, as one line: what a folded system line shows (SPEC.md §4.1). */
export function lastParagraph(text: string): string {
  const paragraphs = codeBlocks(text).filter((block) => !block.code).map((block) => block.text).join("\n\n")
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  return paragraphs[paragraphs.length - 1] ?? "";
}
