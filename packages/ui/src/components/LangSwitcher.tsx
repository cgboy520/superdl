/** 语言切换(两端共用):与两端 i18n.ts 的 SUPPORTED_LANGS 对齐,共享层不反向依赖 app 模块。
 *  浅色面(登录页等)须传 variant="light",默认按顶栏深底渲染;width 按各端顶栏密度传入。 */

import { GlobalOutlined } from "@ant-design/icons";
import { Select } from "antd";
import { useTranslation } from "react-i18next";

const SUPPORTED_LANGS = ["zh-CN", "en-US"] as const;

export function LangSwitcher({
  variant = "dark",
  width = 88,
}: {
  variant?: "dark" | "light";
  width?: number;
}) {
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
      prefix={
        <GlobalOutlined
          style={{ color: variant === "dark" ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.45)" }}
        />
      }
      style={{ width }}
      options={SUPPORTED_LANGS.map((l) => ({ value: l, label: labels[l] }))}
      onChange={(lng) => void i18n.changeLanguage(lng)}
      aria-label={t("lang.switchLabel")}
    />
  );
}
