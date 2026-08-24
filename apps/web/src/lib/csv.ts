/**
 * 客户端 CSV 导出:BOM 头防 Excel 中文乱码;金额列保持后端字符串原样(禁 parseFloat);
 * 含逗号/引号/换行的字段按 RFC4180 转义。
 */

const FORMULA_LEAD = /^[=+@\t\r]/;
const PLAIN_NUMBER = /^-?\d+(\.\d+)?$/;

export function toCsv(
  headers: readonly string[],
  rows: readonly (readonly (string | number | null | undefined)[])[],
): string {
  const esc = (v: string | number | null | undefined): string => {
    let s = v == null ? "" : String(v);
    // 防公式注入:危险前导字符(或以 - 开头且非纯数字)前置单引号按文本处理;
    // 纯数字的负数金额不受影响
    if (FORMULA_LEAD.test(s) || (s.startsWith("-") && !PLAIN_NUMBER.test(s))) {
      s = `'${s}`;
    }
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return "\ufeff" + [headers, ...rows].map((r) => r.map(esc).join(",")).join("\r\n");
}

export function downloadCsv(filename: string, content: string): void {
  // fetch().text() 解码会剥掉服务端 BOM:落盘前统一补回,防 Excel 中文乱码
  const withBom = content.startsWith("\ufeff") ? content : "\ufeff" + content;
  const blob = new Blob([withBom], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
