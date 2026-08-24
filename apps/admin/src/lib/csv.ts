/** 服务端 CSV 落盘:BOM 补回(fetch().text() 解码会剥掉),防 Excel 中文乱码。 */

export function downloadCsv(filename: string, content: string): void {
  const withBom = content.startsWith("﻿") ? content : "﻿" + content;
  const blob = new Blob([withBom], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
