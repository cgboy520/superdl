/** Controlled form field skeleton outside antd Form: label (optional required star) + control + error / hint; unified 4px label spacing and caption hint size. */

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
  /** Field-level error (red text, takes precedence over hint) */
  error?: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  /** Label and control on one line (short fields) */
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
