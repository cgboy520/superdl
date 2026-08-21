import { GlobalOutlined } from "@ant-design/icons";
import { Select } from "antd";
import { useTranslation } from "react-i18next";

import { SUPPORTED_LANGS } from "../../i18n";

/** 语言切换器:en 文案补齐(C7)前仅开发环境显示,避免半成品英文对用户暴露。 */
export function LangSwitcher() {
  const { t, i18n } = useTranslation();
  if (!import.meta.env.DEV) return null;
  const value = i18n.resolvedLanguage === "en-US" ? "en-US" : "zh-CN";
  const labels: Record<(typeof SUPPORTED_LANGS)[number], string> = {
    "zh-CN": t("lang.zh"),
    "en-US": t("lang.en"),
  };
  return (
    <Select
      size="small"
      variant="borderless"
      value={value}
      prefix={<GlobalOutlined style={{ color: "rgba(255,255,255,0.85)" }} />}
      style={{ width: 110 }}
      options={SUPPORTED_LANGS.map((l) => ({ value: l, label: labels[l] }))}
      onChange={(lng) => void i18n.changeLanguage(lng)}
      aria-label="language"
    />
  );
}
