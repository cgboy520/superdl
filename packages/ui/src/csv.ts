/** 服务端 CSV 落盘(两端共用):BOM 补回(fetch().text() 解码会剥掉),防 Excel 中文乱码。 */

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

/** 服务端 CSV 截断标记(与 apps/api core/csvexport.py TRUNCATED_MARKER 一致)。 */
export const TRUNCATED_MARKER = "#SUPERDL_EXPORT_TRUNCATED#";

/** 落盘并判定截断:返回值供调用方提示「已截断,请缩小范围」。 */
export function downloadCsvChecked(filename: string, content: string): "ok" | "truncated" {
  downloadCsv(filename, content);
  return content.includes(TRUNCATED_MARKER) ? "truncated" : "ok";
}
