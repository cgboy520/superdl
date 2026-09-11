/**
 * locale 目录守护(三端 locales.test 共用):zh/en 键集相等(复数后缀归一)、值非空、{{占位符}} 逐键一致、
 * 值 ≠ 展平键路径、en 文案不含 Han 字符(allowCjkInEn 豁免)。
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

/** 复数后缀归一(en _one/_other → 基键)。 */
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

export function assertLocaleParity(
  zh: Catalog,
  en: Catalog,
  opts: { allowEmpty?: boolean; allowCjkInEn?: string[] } = {},
): void {
  const zhFlat = normalize(zh);
  const enFlat = normalize(en);
  const problems: string[] = [];
  for (const k of zhFlat.keys()) if (!enFlat.has(k)) problems.push(`en 缺 ${k}`);
  for (const k of enFlat.keys()) if (!zhFlat.has(k)) problems.push(`zh 缺 ${k}`);
  if (!opts.allowEmpty) {
    for (const [k, v] of zhFlat) if (!v.trim()) problems.push(`zh 空值 ${k}`);
    for (const [k, v] of enFlat) if (!v.trim()) problems.push(`en 空值 ${k}`);
  }
  for (const [k, v] of zhFlat) if (v === k) problems.push(`zh 值等于键名 ${k}`);
  for (const [k, v] of enFlat) if (v === k) problems.push(`en 值等于键名 ${k}`);
  const cjkAllowed = new Set(opts.allowCjkInEn ?? []);
  for (const [k, v] of enFlat) {
    if (!cjkAllowed.has(k) && /[\u4e00-\u9fff]/.test(v)) problems.push(`en 含中文字符 ${k}`);
  }
  for (const [k, zhV] of zhFlat) {
    const enV = enFlat.get(k);
    if (enV !== undefined && placeholders(enV) !== placeholders(zhV)) problems.push(`占位符不一致 ${k}`);
  }
  if (problems.length > 0) throw new Error(`locale parity:\n${problems.join("\n")}`);
}
