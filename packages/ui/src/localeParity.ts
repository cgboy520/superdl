/**
 * locale 目录守护(三端 locales.test 共用):zh/en 键集相等(复数后缀归一)、值非空、{{占位符}} 逐键一致。
 * `i18next-cli extract --ci` 只核对代码里出现的键,preservePatterns 保护的动态键与 shared/errors 目录只有这里守。
 * 另有两条事故防线:值 = 展平键路径(批量生成后未回读的典型痕迹,界面直接渲染 "skus.onSale");
 * en 文案混入 Han 字符 = 漏翻(语言固有名称「中文」经 allowCjkInEn 豁免)。
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
  // 值与展平键路径完全相同 = 批量生成后未回填文案,界面会直接渲染键名
  for (const [k, v] of zhFlat) if (v === k) problems.push(`zh 值等于键名 ${k}`);
  for (const [k, v] of enFlat) if (v === k) problems.push(`en 值等于键名 ${k}`);
  // en 文案混入 Han 字符 = 漏翻(语言固有名称等豁免键经 allowCjkInEn 显式登记)
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
