/** CSV 导出:loading 态 + 截断/成功/失败提示;导出函数由调用方给。时区偏移 UTC 以东为正。 */
import { App } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

type CsvLang = "zh-CN" | "en-US";

export function useCsvExport(run: (tzOffsetMinutes: number, lang: CsvLang) => Promise<"ok" | "truncated">) {
  const { t, i18n } = useTranslation("shared");
  const { message } = App.useApp();
  const [exporting, setExporting] = useState(false);
  const doExport = async () => {
    setExporting(true);
    try {
      const lang: CsvLang = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
      const r = await run(-new Date().getTimezoneOffset(), lang);
      if (r === "truncated") message.warning(t("csv.exportTruncated"));
      else message.success(t("csv.exportDone"));
    } catch {
      message.error(t("csv.exportFailed"));
    } finally {
      setExporting(false);
    }
  };
  return { doExport, exporting };
}
