/** CSV export: loading state + truncation / success / failure messages; the export function comes from the caller. Time-zone offset east of UTC is positive. */
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
