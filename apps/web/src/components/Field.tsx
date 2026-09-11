/** 非 antd Form 的受控表单字段骨架:标签(可带必填星)+ 控件 + 错误 / 说明;统一 4px 标签间距与 caption 说明字号。 */

import { fontSize, space } from "@superdl/ui";
import { Typography } from "antd";
import type { ReactNode } from "react";

export function Field({
  label,
  required,
  error,
  hint,
  children,
  inline,
}: {
  label: ReactNode;
  required?: boolean;
  /** 字段级错误(红字,优先于 hint) */
  error?: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  /** 标签与控件同行(短字段) */
  inline?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: inline ? "row" : "column",
        alignItems: inline ? "center" : "stretch",
        gap: inline ? space.md : space.xs,
        width: "100%",
      }}
    >
      <Typography.Text type="secondary" style={{ flexShrink: 0 }}>
        {required && (
          <span aria-hidden style={{ color: "var(--sdl-color-required)", marginInlineEnd: 4 }}>
            *
          </span>
        )}
        {label}
      </Typography.Text>
      {children}
      {error ? (
        <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
          {error}
        </Typography.Text>
      ) : hint ? (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {hint}
        </Typography.Text>
      ) : null}
    </div>
  );
}
