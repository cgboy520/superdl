/** Save a server CSV to disk (shared by both consoles), restoring the BOM. */

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
