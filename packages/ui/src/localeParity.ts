/**
 * locale 目录守护(三端 locales.test 共用):zh/en 键集相等(复数后缀归一)、值非空、
 * {{占位符}} 逐键一致。`i18next-cli extract --ci` 只核对代码里出现的键,preservePatterns
 * 保护的动态键与 shared/errors 目录只有这里守。不依赖测试框架:问题一次性全部抛出。
 */

type Catalog = Record<string, unknown>;

function flatten(obj: Catalog, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") out.set(key, v);
    else if (v && typeof v === "object") {
      for (const [ck, cv] of flatten(v as Catalog, key)) out.set(ck, cv);
    }
  }
  return out;
}

/** en 用 _one/_other 复数后缀而 zh 用基键;比较前归一。 */
function normalize(obj: Catalog): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of flatten(obj)) out.set(k.replace(/_(one|other|zero|two|few|many)$/, ""), v);
  return out;
}

function placeholders(value: string): string {
  return [...value.matchAll(/\{\{(\w+)\}\}/g)]
    .map((m) => m[1])
    .sort()
    .join(",");
}

export function assertLocaleParity(zh: Catalog, en: Catalog, opts: { allowEmpty?: boolean } = {}): void {
  const zhFlat = normalize(zh);
  const enFlat = normalize(en);
  const problems: string[] = [];
  for (const k of zhFlat.keys()) if (!enFlat.has(k)) problems.push(`en 缺 ${k}`);
  for (const k of enFlat.keys()) if (!zhFlat.has(k)) problems.push(`zh 缺 ${k}`);
  if (!opts.allowEmpty) {
    for (const [k, v] of zhFlat) if (!v.trim()) problems.push(`zh 空值 ${k}`);
    for (const [k, v] of enFlat) if (!v.trim()) problems.push(`en 空值 ${k}`);
  }
  for (const [k, zhV] of zhFlat) {
    const enV = enFlat.get(k);
    if (enV !== undefined && placeholders(enV) !== placeholders(zhV)) problems.push(`占位符不一致 ${k}`);
  }
  if (problems.length > 0) throw new Error(`locale parity:\n${problems.join("\n")}`);
}
