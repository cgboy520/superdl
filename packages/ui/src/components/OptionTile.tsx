/** 单选 tile 组:方向键切换,禁用项保持可见并显示原因。 */

import { CheckCircleFilled } from "@ant-design/icons";
import { Tooltip, theme, Typography } from "antd";
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { useThemeColors } from "../hooks/useThemeColors";
import { fontSize, fontWeight, iconSize, layout, motion, shadow, space } from "../tokens";

export interface OptionTileOption<T extends string> {
  value: T;
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
  /** 不可用原因;非空即门控 */
  reason?: ReactNode;
}

export function OptionTileGroup<T extends string>({
  label,
  value,
  onChange,
  options,
  columns = "auto",
  size = "md",
  required,
  error,
  hideLabel,
}: {
  label: ReactNode;
  value: T | undefined;
  onChange: (v: T) => void;
  options: OptionTileOption<T>[];
  columns?: 1 | 2 | 3 | 4 | "auto";
  size?: "sm" | "md";
  required?: boolean;
  error?: ReactNode;
  /** 只做 aria 标签,不渲染可见标题 */
  hideLabel?: boolean;
}) {
  const { token } = theme.useToken();
  const colors = useThemeColors();
  const labelId = useId();
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  const enabled = options.map((o, i) => (o.reason ? -1 : i)).filter((i) => i >= 0);
  const selectedIndex = options.findIndex((o) => o.value === value);

  const move = (from: number, dir: 1 | -1) => {
    if (enabled.length === 0) return;
    const pos = enabled.indexOf(from);
    const next = enabled[(pos + dir + enabled.length) % enabled.length] ?? enabled[0];
    if (next === undefined) return;
    const opt = options[next];
    if (!opt) return;
    onChange(opt.value);
    refs.current[next]?.focus();
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>, i: number) => {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      move(i, 1);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      move(i, -1);
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      const opt = options[i];
      if (opt && !opt.reason) onChange(opt.value);
    }
  };

  const gridColumns =
    columns === "auto" ? "repeat(auto-fill, minmax(220px, 1fr))" : `repeat(${columns}, minmax(0, 1fr))`;
  const padding = size === "sm" ? `${space.sm}px ${space.md}px` : `${space.md}px ${space.lg}px`;

  return (
    <div>
      {!hideLabel && (
        <Typography.Text id={labelId} type="secondary" style={{ display: "block", marginBottom: space.sm }}>
          {required && (
            <span aria-hidden style={{ color: token.colorError, marginInlineEnd: 4 }}>
              *
            </span>
          )}
          {label}
        </Typography.Text>
      )}
      <div
        role="radiogroup"
        aria-labelledby={hideLabel ? undefined : labelId}
        aria-label={hideLabel && typeof label === "string" ? label : undefined}
        style={{ display: "grid", gridTemplateColumns: gridColumns, gap: space.md }}
      >
        {options.map((o, i) => {
          const selected = o.value === value;
          const gated = Boolean(o.reason);
          const tabbable = selected || (selectedIndex < 0 && enabled[0] === i);
          const tile = (
            <div
              key={o.value}
              ref={(el) => {
                refs.current[i] = el;
              }}
              role="radio"
              aria-checked={selected}
              aria-disabled={gated || undefined}
              tabIndex={gated ? -1 : tabbable ? 0 : -1}
              className="focus-ring"
              onClick={() => {
                if (!gated) onChange(o.value);
              }}
              onKeyDown={(e) => {
                if (!gated) onKey(e, i);
              }}
              style={{
                position: "relative",
                display: "flex",
                gap: space.md,
                alignItems: "flex-start",
                padding,
                minHeight: size === "sm" ? 48 : 64,
                borderRadius: layout.cardRadius - 2,
                border: `1px solid ${selected ? colors.primary : token.colorBorder}`,
                background: gated
                  ? token.colorBgContainerDisabled
                  : selected
                    ? colors.primarySoft
                    : token.colorBgContainer,
                color: gated ? token.colorTextDisabled : token.colorText,
                cursor: gated ? "not-allowed" : "pointer",
                boxShadow: selected ? "none" : undefined,
                transition: `border-color ${motion.normal}s, background ${motion.normal}s, box-shadow ${motion.normal}s`,
              }}
              onMouseEnter={(e) => {
                if (!gated && !selected)
                  e.currentTarget.style.boxShadow = colors.mode === "dark" ? shadow.dark.sm : shadow.light.sm;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.boxShadow = "none";
              }}
            >
              {o.icon && (
                <span
                  style={{
                    fontSize: iconSize.lg,
                    lineHeight: 1,
                    color: selected ? colors.primary : "inherit",
                    marginTop: 2,
                  }}
                >
                  {o.icon}
                </span>
              )}
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: space.sm, fontWeight: fontWeight.medium }}>
                  <span>{o.title}</span>
                  {o.badge}
                </div>
                {o.description && (
                  <div
                    style={{
                      fontSize: fontSize.caption,
                      color: gated ? token.colorTextDisabled : token.colorTextSecondary,
                      marginTop: 2,
                    }}
                  >
                    {o.description}
                  </div>
                )}
                {gated && (
                  <div style={{ fontSize: fontSize.caption, color: token.colorTextDisabled, marginTop: 2 }}>
                    {o.reason}
                  </div>
                )}
              </div>
              {selected && (
                <CheckCircleFilled
                  aria-hidden
                  style={{
                    position: "absolute",
                    top: space.sm,
                    right: space.sm,
                    color: colors.primary,
                    fontSize: iconSize.md,
                  }}
                />
              )}
            </div>
          );
          return gated ? (
            <Tooltip key={o.value} title={o.reason}>
              {tile}
            </Tooltip>
          ) : (
            tile
          );
        })}
      </div>
      {error && (
        <Typography.Text type="danger" style={{ display: "block", marginTop: space.xs, fontSize: fontSize.caption }}>
          {error}
        </Typography.Text>
      )}
    </div>
  );
}
