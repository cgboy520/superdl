import { GlobalOutlined } from "@ant-design/icons";
import { Select } from "antd";
import { useTranslation } from "react-i18next";

import { SUPPORTED_LANGS } from "../../i18n";

/** 语言切换器(顶栏深色 / 登录页等浅色面用 variant="light")。 */
export function LangSwitcher({ variant = "dark" }: { variant?: "dark" | "light" }) {
  const { t, i18n } = useTranslation();
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
      prefix={
        <GlobalOutlined
          style={{ color: variant === "dark" ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.45)" }}
        />
      }
      style={{ width: 88 }}
      options={SUPPORTED_LANGS.map((l) => ({ value: l, label: labels[l] }))}
      onChange={(lng) => void i18n.changeLanguage(lng)}
      aria-label={t("lang.switchLabel")}
    />
  );
}
