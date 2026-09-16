/**
 * Locale directory guard (shared by the three locales.test files): equal zh/en key sets (plural suffixes normalised), non-empty values, {{placeholders}} equal per key,
 * value ≠ flattened key path, en copy without Han characters (allowCjkInEn exempts).
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

/** Normalise plural suffixes (en _one/_other → base key; the language set is only zh/en, so only these two suffixes occur). */
function normalize(obj: Catalog): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of flatten(obj)) out.set(k.replace(/_(one|other)$/, ""), v);
  return out;
}

function placeholders(value: string): string {
  return [...value.matchAll(/\{\{(\w+)\}\}/g)]
    .map((m) => m[1])
    .sort()
    .join(",");
}

export function assertLocaleParity(zh: Catalog, en: Catalog, opts: { allowCjkInEn?: string[] } = {}): void {
  const zhFlat = normalize(zh);
  const enFlat = normalize(en);
  const problems: string[] = [];
  for (const k of zhFlat.keys()) if (!enFlat.has(k)) problems.push(`en missing ${k}`);
  for (const k of enFlat.keys()) if (!zhFlat.has(k)) problems.push(`zh missing ${k}`);
  for (const [k, v] of zhFlat) if (!v.trim()) problems.push(`zh empty ${k}`);
  for (const [k, v] of enFlat) if (!v.trim()) problems.push(`en empty ${k}`);
  for (const [k, v] of zhFlat) if (v === k) problems.push(`zh value equals key ${k}`);
  for (const [k, v] of enFlat) if (v === k) problems.push(`en value equals key ${k}`);
  const cjkAllowed = new Set(opts.allowCjkInEn ?? []);
  for (const [k, v] of enFlat) {
    if (!cjkAllowed.has(k) && /[\u4e00-\u9fff]/.test(v)) problems.push(`en contains Chinese characters ${k}`);
  }
  for (const [k, zhV] of zhFlat) {
    const enV = enFlat.get(k);
    if (enV !== undefined && placeholders(enV) !== placeholders(zhV)) problems.push(`placeholder mismatch ${k}`);
  }
  if (problems.length > 0) throw new Error(`locale parity:\n${problems.join("\n")}`);
}
