/** CSV 导出三件套:loading 态 + 截断/成功/失败提示;导出函数由调用方给(api.ts 的 export*Csv)。 */
import { App } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

type CsvLang = "zh-CN" | "en-US";

export function useCsvExport(run: (tzOffsetMinutes: number, lang: CsvLang) => Promise<"ok" | "truncated">) {
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();
  const [exporting, setExporting] = useState(false);
  const doExport = async () => {
    setExporting(true);
    try {
      const lang: CsvLang = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
      const r = await run(-new Date().getTimezoneOffset(), lang);
      if (r === "truncated") message.warning(t("common.csvTruncated"));
      else message.success(t("common.csvExported"));
    } catch {
      message.error(t("common.csvExportFailed"));
    } finally {
      setExporting(false);
    }
  };
  return { doExport, exporting };
}
