/** Save a CSV to disk (shared by both consoles), restoring the BOM; build client-side ones with csvOf. */

function downloadCsv(filename: string, content: string): void {
  const withBom = content.startsWith("﻿") ? content : "﻿" + content;
  const blob = new Blob([withBom], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Server CSV truncation marker (matches TRUNCATED_MARKER in apps/api core/csvexport.py). */
export const TRUNCATED_MARKER = "#SUPERDL_EXPORT_TRUNCATED#";

/** Save to disk and return whether it was truncated. */
export function downloadCsvChecked(filename: string, content: string): "ok" | "truncated" {
  downloadCsv(filename, content);
  return content.includes(TRUNCATED_MARKER) ? "truncated" : "ok";
}

/** One CSV cell: quoted when it holds a comma, quote or line break; a leading = + - @ or control character gets a ' so spreadsheets do not run it as a formula. */
export function csvCell(value: string): string {
  const guarded = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
}

export function csvOf(rows: readonly (readonly string[])[]): string {
  return rows.map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** Save a CSV built in the browser (no truncation marker to check). */
export function saveCsv(filename: string, content: string): void {
  downloadCsv(filename, content);
}
