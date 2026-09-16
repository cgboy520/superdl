/** Copyable value with monospace / code rendering and a toggleable masked state. */

import { CheckOutlined, CopyOutlined, EyeInvisibleOutlined, EyeOutlined } from "@ant-design/icons";
import { App, Button, Space, Tooltip, Typography } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { space } from "../tokens";

export function CopyButton({
  text,
  label,
  ariaLabel,
  size = "small",
  onCopied,
}: {
  text: string;
  label?: ReactNode;
  ariaLabel?: string;
  size?: "small" | "middle";
  onCopied?: () => void;
}) {
  const { message } = App.useApp();
  const { t } = useTranslation("shared");
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size={size}
      aria-label={ariaLabel ?? t("common.copy")}
      icon={copied ? <CheckOutlined /> : <CopyOutlined />}
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          message.success(t("common.copied"));
          onCopied?.();
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {label}
    </Button>
  );
}

export function CopyField({
  value,
  display,
  label,
  mono = true,
  code = false,
  secret = false,
  block = false,
  size = "small",
  testId,
  onCopied,
}: {
  value: string;
  /** Display text (default = value) */
  display?: ReactNode;
  /** Copy button text */
  label?: ReactNode;
  mono?: boolean;
  /** Render as <code>, default span. */
  code?: boolean;
  /** Masked until the eye is clicked */
  secret?: boolean;
  block?: boolean;
  size?: "small" | "middle";
  /** data-testid of the value node. */
  testId?: string;
  onCopied?: () => void;
}) {
  const { t } = useTranslation("shared");
  const [revealed, setRevealed] = useState(!secret);
  const shown = revealed ? (display ?? value) : "•".repeat(Math.min(value.length, 24));
  const text = code ? (
    <Typography.Text code className={mono ? "mono" : undefined} style={{ wordBreak: "break-all" }} data-testid={testId}>
      {shown}
    </Typography.Text>
  ) : (
    <span className={mono ? "mono" : undefined} style={{ wordBreak: "break-all" }} data-testid={testId}>
      {shown}
    </span>
  );
  return (
    <Space size={space.sm} align="center" wrap style={block ? { display: "flex", width: "100%" } : undefined}>
      {text}
      {secret && (
        <Tooltip title={revealed ? t("common.hide") : t("common.reveal")}>
          <Button
            type="text"
            size={size}
            aria-label={revealed ? t("common.hide") : t("common.reveal")}
            icon={revealed ? <EyeInvisibleOutlined /> : <EyeOutlined />}
            onClick={() => setRevealed((r) => !r)}
          />
        </Tooltip>
      )}
      <CopyButton text={value} label={label} size={size} onCopied={onCopied} />
    </Space>
  );
}
