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
    // \u9632 CSV \u516c\u5f0f\u6ce8\u5165:\u7528\u6237\u53ef\u63a7\u5b57\u6bb5(\u5b9e\u4f8b\u540d/\u5907\u6ce8)\u4ee5 =/+/@ \u5f00\u5934,\u6216\u4ee5 - \u5f00\u5934\u4e14\u975e\u7eaf\u6570\u5b57\u65f6,
    // \u524d\u7f6e\u5355\u5f15\u53f7\u8ba9\u7535\u5b50\u8868\u683c\u6309\u6587\u672c\u5904\u7406;\u8d1f\u6570\u91d1\u989d(\u7eaf\u6570\u5b57)\u4e0d\u53d7\u5f71\u54cd
    if (FORMULA_LEAD.test(s) || (s.startsWith("-") && !PLAIN_NUMBER.test(s))) {
      s = `'${s}`;
    }
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return "\ufeff" + [headers, ...rows].map((r) => r.map(esc).join(",")).join("\r\n");
}

export function downloadCsv(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
