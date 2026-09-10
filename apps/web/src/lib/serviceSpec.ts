/** 服务容器规格的常量与纯函数校验:与后端 orchestrator/schemas.py 同源,创建页与部署页共用。
 *  后端是硬闸,这里只提前给反馈;判据改了必须两边一起改。 */

/** 与后端 schemas._ENV_NAME_RE 同源:容器环境变量名的形态 */
export const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
/** 与后端 _RESERVED_ENV_PREFIXES / _RESERVED_ENV_NAMES 同源:平台自己往容器里注入的名段 */
export const RESERVED_ENV_PREFIXES = ["JUPYTER_", "SUPERDL_"];
export const RESERVED_ENV_NAMES = ["AUTHORIZED_KEYS"];
/** 与后端 RESERVED_SERVICE_PORTS 同源:22 = sshd,8888 = JupyterLab */
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

/**
 * 引用是否钉死到具体版本。判据必须与后端 core.registry.is_pinned_image_ref 一致
 * (不写 tag = 隐含 latest,同样不算钉死)。
 */
export function isPinnedImageRef(ref: string): boolean {
  if (ref.includes("@sha256:")) return true;
  // 冒号也可能是仓库主机的端口(registry:5000/img),tag 只看最后一段路径
  const last = ref.split("/").pop() ?? "";
  const colon = last.lastIndexOf(":");
  return colon > 0 && last.slice(colon + 1) !== "latest";
}

export type EnvNameIssue = "invalid" | "reserved" | "duplicate";

/** 单个变量名的形态问题(不含重名,重名要看整表:envRowIssue)。 */
export function envNameIssue(name: string): Exclude<EnvNameIssue, "duplicate"> | null {
  if (!ENV_NAME_RE.test(name)) return "invalid";
  if (RESERVED_ENV_NAMES.includes(name) || RESERVED_ENV_PREFIXES.some((pre) => name.startsWith(pre))) {
    return "reserved";
  }
  return null;
}

/** 某一行在整表里的问题(与后端 model_validator 同款判据);空名不算错,提交时会被过滤掉。 */
export function envRowIssue(row: EnvRow, rows: readonly EnvRow[]): EnvNameIssue | null {
  const key = row.name.trim();
  if (key === "") return null;
  const shape = envNameIssue(key);
  if (shape) return shape;
  if (rows.filter((r) => r.name.trim() === key).length > 1) return "duplicate";
  return null;
}

/** 名字非空的行才算数;同名以最后一行为准,但重名会先被 envRowIssue 拦下。 */
export function envEntriesOf(rows: readonly EnvRow[]): EnvRow[] {
  return rows.map((r) => ({ ...r, name: r.name.trim() })).filter((r) => r.name !== "");
}

/** 批量粘贴 KEY=VALUE(一行一条):非法名 / 保留名 / 与已有或本批重名的行跳过并计数。 */
export function parseEnvBulk(
  text: string,
  existingNames: Iterable<string>,
): { rows: EnvRow[]; skipped: number } {
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

/** 批量粘贴启动参数(一行一个),空行忽略。 */
export function parseArgBulk(text: string): ArgRow[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .map((value) => ({ id: newRowId(), value }));
}

/** 启动命令按空格拆成 exec 形式:容器 command 不经 shell,整串带空格会被当成一个可执行文件名。 */
export function commandToList(command: string): string[] {
  const s = command.trim();
  return s ? s.split(/\s+/) : [];
}
