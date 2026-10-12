import { useRef } from "react";
import type { ComponentProps, MouseEvent } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { shownReference } from "../shown";
import type { ShownImage } from "../data/api";
import { Icon, SavedImage } from "./Shown";
import "./markdown.css";

/** GitHub's sanitizing schema, narrowed: no responsive or media sources, forms, inline styles or keyboard shortcuts.
 * Event attributes, scripts, iframes and styles are already outside it. */
const SCHEMA = {
  ...defaultSchema,
  tagNames: defaultSchema.tagNames!.filter((tag) => !["picture", "source"].includes(tag)),
  attributes: {
    ...Object.fromEntries(Object.entries(defaultSchema.attributes!).filter(([tag]) => tag !== "source")),
    img: ["ariaDescribedBy", "ariaLabel", "ariaLabelledBy", "src"],
    "*": defaultSchema.attributes!["*"]!.filter((name) =>
      !["accept", "acceptCharset", "accessKey", "action", "encType", "method", "tabIndex"].includes(String(name))),
  },
};

interface Hast { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: Hast[] }

function host(url: string): string {
  try { return new URL(url).host; } catch { return url; }
}

function text(node: Hast): string {
  return node.value ?? (node.children ?? []).map(text).join("");
}

/**
 * After sanitizing, every image becomes one of three things: a copied image at its saved address on this connection,
 * a link to a remote image (never loaded), or the "Image not shown" box. Headings gain GitHub's anchors.
 */
function shownImages({ path, images }: { path: string; images: ShownImage[] }) {
  return (tree: Hast) => {
    const slugs = new Map<string, number>();
    const walk = (node: Hast, linked: boolean) => {
      const task = node.tagName === "li" && String(node.properties?.className ?? "").includes("task-list-item");
      node.children = node.children?.flatMap((child): Hast[] => {
        if (child.type !== "element") return [child];
        // A disabled checkbox is a task list's own; the sanitizer would turn any other input into one.
        if (child.tagName === "input" && !task) return [];
        const properties = child.properties ??= {};
        if (child.tagName === "img") {
          const src = String(properties.src ?? "");
          const alt = String(properties.alt ?? "");
          const target = src ? shownReference(src, path) : undefined;
          const index = typeof target === "string" ? images.findIndex((image) => image.path === target) : -1;
          if (index >= 0) return [{ ...child, properties: { src: images[index]!.url, alt, dataShown: index, dataLinked: linked || undefined } }];
          if (/^https?:\/\//i.test(src)) {
            const label: Hast = { type: "element", tagName: "span", properties: { dataRemote: src }, children: [{ type: "text", value: alt || host(src) }] };
            return [linked ? label : { type: "element", tagName: "a", properties: { href: src }, children: [label] }];
          }
          return [{ type: "element", tagName: "span", properties: { dataMissing: typeof target === "string" ? target : "" }, children: [] }];
        }
        if (/^h[1-6]$/.test(child.tagName ?? "") && !properties.id) {
          const base = text(child).toLowerCase().trim().replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "").replace(/ /g, "-");
          const seen = slugs.get(base) ?? 0;
          slugs.set(base, seen + 1);
          properties.id = `user-content-${seen ? `${base}-${seen}` : base}`;
        }
        walk(child, linked || child.tagName === "a");
        return [child];
      });
    };
    walk(tree, false);
  };
}

/**
 * A markdown document rendered as GitHub shows it, inert: links open in a new tab or scroll within the document,
 * relative links are text, code has no actions, and only the shown set's own saved images load (SPEC.md §3.11).
 * Without `images`, every local image is the "Image not shown" box.
 */
export default function MarkdownDocument({ text: source, path = "", images = [], onOpenImage, showLinkTargets = false }: {
  text: string; path?: string; images?: ShownImage[]; onOpenImage?: (index: number) => void; showLinkTargets?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const scroll = (event: MouseEvent, fragment: string) => {
    event.preventDefault();
    const id = decodeURIComponent(fragment);
    const target = root.current?.querySelector(`[id="${CSS.escape(`user-content-${id}`)}"], [id="${CSS.escape(id)}"]`);
    target?.scrollIntoView({ block: "start" });
  };
  const components: Components = {
    a({ href = "", children }) {
      if (/^(https?|mailto):/i.test(href)) {
        const label = typeof children === "string" ? children : null;
        return <a href={href} target="_blank" rel="noopener noreferrer">{children}{showLinkTargets && label !== href ? ` (${href})` : null}</a>;
      }
      if (href.startsWith("#") && href.length > 1) return <a href={href} onClick={(event) => scroll(event, href.slice(1))}>{children}</a>;
      return <span className="md-relative" title={href || undefined}>{children}</span>;
    },
    img(props) {
      const index = Number((props as Record<string, unknown>)["data-shown"]);
      const image = images[index];
      if (!image) return null;
      const linked = Boolean((props as Record<string, unknown>)["data-linked"]);
      return <SavedImage className="md-picture" image={image} alt={props.alt || image.name}
        open={linked || !onOpenImage ? undefined : () => onOpenImage(index)} />;
    },
    span(props: ComponentProps<"span"> & { node?: unknown }) {
      const { node: _node, children, ...rest } = props;
      const data = rest as Record<string, unknown>;
      if (typeof data["data-remote"] === "string") return <span className="md-remote"><Icon name="image" />{children}</span>;
      if (typeof data["data-missing"] === "string") {
        return <span className="md-not-shown"><Icon name="image-off" />
          <span>Image not shown{data["data-missing"] ? <code>{String(data["data-missing"])}</code> : null}</span>
        </span>;
      }
      return <span {...rest}>{children}</span>;
    },
    table({ node: _node, ...props }) {
      return <div className="md-table"><table {...props} /></div>;
    },
  };
  return <div className="markdown" ref={root}>
    <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw, [rehypeSanitize, SCHEMA], [shownImages, { path, images }]]}
      components={components}>{source}</Markdown>
  </div>;
}
