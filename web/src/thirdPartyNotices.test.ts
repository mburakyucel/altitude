import { describe, expect, it } from "vitest";

// THIRD_PARTY_NOTICES.md ships in the release archive and names every production dependency of web/ with its
// copyright, beside the text of each licence they use. This walks the installed dependency tree the way Node
// resolves it, so a package added or updated without its notice fails here instead of shipping unattributed.

// The web UI's code runs in the browser and has no Node type declarations, so the test types the calls it uses.
declare const process: {
  getBuiltinModule(id: "node:fs"): {
    existsSync(path: string): boolean;
    readFileSync(path: string, encoding: "utf8"): string;
    realpathSync(path: string): string;
  };
  getBuiltinModule(id: "node:path"): { dirname(path: string): string; join(...paths: string[]): string };
};
declare const __dirname: string;
const { existsSync, readFileSync, realpathSync } = process.getBuiltinModule("node:fs");
const { dirname, join } = process.getBuiltinModule("node:path");
const web = join(__dirname, "..");
const notices = readFileSync(join(web, "..", "THIRD_PARTY_NOTICES.md"), "utf8");

// The heading of each licence text the notices reproduce, by the SPDX identifier in a package's package.json.
const licenceTexts: Record<string, string> = {
  MIT: "## MIT License",
  ISC: "### ISC License",
  "BSD-2-Clause": "### BSD 2-Clause License",
  "OFL-1.1": "### SIL Open Font License 1.1",
};

type Manifest = {
  name: string;
  license?: string;
  dependencies?: Record<string, string>;
  optionalDependencies?: Record<string, string>;
  peerDependencies?: Record<string, string>;
};

function resolve(name: string, from: string): string | undefined {
  for (let dir = realpathSync(from); ; dir = dirname(dir)) {
    const candidate = join(dir, "node_modules", name);
    if (existsSync(join(candidate, "package.json"))) return realpathSync(candidate);
    if (dirname(dir) === dir) return undefined;
  }
}

// Optional and peer dependencies count only where they are installed, as `pnpm list --prod` counts them.
function productionPackages(): Map<string, Manifest> {
  const found = new Map<string, Manifest>();
  const root = JSON.parse(readFileSync(join(web, "package.json"), "utf8")) as Manifest;
  const queue = Object.keys(root.dependencies ?? {}).map((name) => ({ name, from: web, required: true }));
  const seen = new Set<string>();
  for (let next = queue.shift(); next; next = queue.shift()) {
    const dir = resolve(next.name, next.from);
    if (!dir) {
      if (next.required) throw new Error(`${next.name} is not installed; run pnpm install in web/`);
      continue;
    }
    if (seen.has(dir)) continue;
    seen.add(dir);
    const manifest = JSON.parse(readFileSync(join(dir, "package.json"), "utf8")) as Manifest;
    found.set(`${manifest.name}@${dir}`, manifest);
    for (const name of Object.keys(manifest.dependencies ?? {})) queue.push({ name, from: dir, required: true });
    for (const name of Object.keys({ ...manifest.optionalDependencies, ...manifest.peerDependencies })) {
      queue.push({ name, from: dir, required: false });
    }
  }
  return found;
}

// The names in the first column of every table, where a cell lists one or more packages separated by commas.
function listedNames(): Set<string> {
  const names = new Set<string>();
  for (const line of notices.split("\n")) {
    const cell = /^\| ([^|]+?) \|/.exec(line)?.[1];
    if (!cell) continue;
    for (const name of cell.split(", ")) names.add(name.replace(/\s*\(.*\)$/, ""));
  }
  return names;
}

describe("third-party notices", () => {
  const packages = [...productionPackages().values()];

  it("names every production package of the web UI", () => {
    const listed = listedNames();
    const missing = [...new Set(packages.map((p) => p.name))].filter((name) => !listed.has(name)).sort();
    expect(missing).toEqual([]);
  });

  it("reproduces the text of every licence they use", () => {
    const unknown = packages.filter((p) => !(p.license && p.license in licenceTexts));
    expect(unknown.map((p) => `${p.name}: ${p.license}`)).toEqual([]);
    const headings = new Set(packages.map((p) => licenceTexts[p.license!]!));
    const absent = [...headings].filter((heading) => !notices.split("\n").includes(heading));
    expect(absent).toEqual([]);
  });
});
