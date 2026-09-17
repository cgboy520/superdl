/** Input constraints, pure validation and submit payload conversion of service containers. */

/** Same source as the backend schemas._ENV_NAME_RE */
export const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
/** Same source as the backend _RESERVED_ENV_PREFIXES / _RESERVED_ENV_NAMES: names injected by the platform */
export const RESERVED_ENV_PREFIXES = ["JUPYTER_", "SUPERDL_"];
export const RESERVED_ENV_NAMES = ["AUTHORIZED_KEYS"];
/** Same source as the backend RESERVED_SERVICE_PORTS: 22 = sshd, 8888 = JupyterLab */
export const RESERVED_SERVICE_PORTS = [22, 8888];

export interface ArgRow {
  id: string;
  value: string;
}

export interface EnvRow {
  id: string;
  name: string;
  value: string;
  secret: boolean;
}

export function newRowId(): string {
  return crypto.randomUUID();
}

/** Whether the reference pins a concrete version, the same criterion as the backend core.registry.is_pinned_image_ref (no tag does not count as pinned). */
export function isPinnedImageRef(ref: string): boolean {
  if (ref.includes("@sha256:")) return true;
  const last = ref.split("/").pop() ?? "";
  const colon = last.lastIndexOf(":");
  return colon > 0 && last.slice(colon + 1) !== "latest";
}

export type EnvNameIssue = "invalid" | "reserved" | "duplicate";

/** Shape issue of a single variable name (duplicates are envRowIssue's job). */
export function envNameIssue(name: string): Exclude<EnvNameIssue, "duplicate"> | null {
  if (!ENV_NAME_RE.test(name)) return "invalid";
  if (RESERVED_ENV_NAMES.includes(name) || RESERVED_ENV_PREFIXES.some((pre) => name.startsWith(pre))) {
    return "reserved";
  }
  return null;
}

/** Issue of one row within the whole table (same criteria as the backend model_validator); an empty name is no error, filtered on submit. */
export function envRowIssue(row: EnvRow, rows: readonly EnvRow[]): EnvNameIssue | null {
  const key = row.name.trim();
  if (key === "") return null;
  const shape = envNameIssue(key);
  if (shape) return shape;
  if (rows.filter((r) => r.name.trim() === key).length > 1) return "duplicate";
  return null;
}

/** Only rows with a non-empty name count; the last row wins for duplicate names. */
export function envEntriesOf(rows: readonly EnvRow[]): EnvRow[] {
  return rows.map((r) => ({ ...r, name: r.name.trim() })).filter((r) => r.name !== "");
}

/** Bulk paste of KEY=VALUE (one per line): invalid / reserved / duplicate rows are skipped and counted. */
export function parseEnvBulk(text: string, existingNames: Iterable<string>): { rows: EnvRow[]; skipped: number } {
  const existing = new Set(existingNames);
  const seen = new Set<string>();
  const rows: EnvRow[] = [];
  let skipped = 0;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line === "") continue;
    const eq = line.indexOf("=");
    const name = (eq >= 0 ? line.slice(0, eq) : line).trim();
    const value = eq >= 0 ? line.slice(eq + 1) : "";
    if (envNameIssue(name) != null || existing.has(name) || seen.has(name)) {
      skipped += 1;
      continue;
    }
    seen.add(name);
    rows.push({ id: newRowId(), name, value, secret: false });
  }
  return { rows, skipped };
}

/** Bulk paste of command arguments (one per line), blank lines ignored. */
export function parseArgBulk(text: string): ArgRow[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .map((value) => ({ id: newRowId(), value }));
}

/** Split the command on spaces into exec form (the container command runs without a shell). */
export function commandToList(command: string): string[] {
  const s = command.trim();
  return s ? s.split(/\s+/) : [];
}

/** The env triple of a revision update: table rows → env / env_secret_keys; still "kept" secret keys → env_secret_keep; the table's new value wins for duplicate names (the backend judges alike). */
export function buildRevisionEnv(
  rows: readonly EnvRow[],
  keepKeys: readonly string[],
): {
  env: Record<string, string> | null;
  env_secret_keys: string[] | null;
  env_secret_keep: string[];
} {
  const entries = envEntriesOf(rows);
  const names = new Set(entries.map((r) => r.name));
  const secret = entries.filter((r) => r.secret).map((r) => r.name);
  return {
    env: entries.length > 0 ? Object.fromEntries(entries.map((r) => [r.name, r.value])) : null,
    env_secret_keys: secret.length > 0 ? secret : null,
    env_secret_keep: keepKeys.filter((k) => !names.has(k)),
  };
}
