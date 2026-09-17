/** Language switcher (shared by both consoles); pass variant="light" on light surfaces. */

import { GlobalOutlined } from "@ant-design/icons";
import { Select } from "antd";
import { useTranslation } from "react-i18next";

import { SUPPORTED_LANGS } from "../i18n";

export function LangSwitcher({ variant = "dark", width = 88 }: { variant?: "dark" | "light"; width?: number }) {
  const { t, i18n } = useTranslation("shared");
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
      prefix={<GlobalOutlined style={{ color: variant === "dark" ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.45)" }} />}
      style={{ width }}
      options={SUPPORTED_LANGS.map((l) => ({ value: l, label: labels[l] }))}
      onChange={(lng) => void i18n.changeLanguage(lng)}
      aria-label={t("lang.switchLabel")}
    />
  );
}
