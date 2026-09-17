/** Metric card (shared by both consoles): title / big number (one text node) / sub-line / trend; value undefined renders a skeleton; link wraps the whole card into a clickable deep link (hover border + top-right arrow). */

import { ArrowUpOutlined, ArrowDownOutlined, ArrowRightOutlined, QuestionCircleOutlined } from "@ant-design/icons";
import { Card, Skeleton, Space, Tooltip, Typography } from "antd";
import { useState, type ReactNode } from "react";

import { useThemeColors } from "../hooks/useThemeColors";
import { fontSize, fontWeight, iconSize, motion, space } from "../tokens";

export interface StatCardProps {
  title: ReactNode;
  /** undefined = loading (skeleton) */
  value: ReactNode | undefined;
  prefix?: ReactNode;
  suffix?: ReactNode;
  /** ? tooltip next to the title */
  hint?: ReactNode;
  trend?: { text: ReactNode; direction: "up" | "down" | "flat" };
  /** Sub-line (comparison / denominator / note) */
  footer?: ReactNode;
  tone?: "default" | "positive" | "negative" | "warning";
  /** When given the whole card is clickable: the caller's Link wraps the content */
  link?: (children: ReactNode) => ReactNode;
  ariaLabel?: string;
  /** Error bar replacing the body on load failure */
  error?: ReactNode;
  /** Big number size: kpi (default) / sectionTitle (several cards side by side) */
  valueSize?: "kpi" | "sectionTitle";
}

export function StatCard({
  title,
  value,
  prefix,
  suffix,
  hint,
  trend,
  footer,
  tone = "default",
  link,
  ariaLabel,
  error,
  valueSize = "kpi",
}: StatCardProps) {
  const colors = useThemeColors();
  const [hover, setHover] = useState(false);
  const toneColor =
    tone === "positive"
      ? colors.positive
      : tone === "negative"
        ? colors.negative
        : tone === "warning"
          ? colors.warning
          : undefined;
  const trendColor =
    trend?.direction === "up" ? colors.positive : trend?.direction === "down" ? colors.negative : colors.textSecondary;
  const inner = (
    <div style={{ display: "flex", flexDirection: "column", gap: space.xs, minWidth: 0 }}>
      <Space size={space.xs} align="center">
        <Typography.Text type="secondary" style={{ fontSize: fontSize.body }}>
          {title}
        </Typography.Text>
        {hint && (
          <Tooltip title={hint}>
            <QuestionCircleOutlined
              tabIndex={0}
              className="focus-ring"
              style={{ fontSize: iconSize.sm, color: colors.textSecondary, cursor: "help" }}
            />
          </Tooltip>
        )}
      </Space>
      {error ? (
        error
      ) : value === undefined ? (
        <Skeleton active title={{ width: "50%" }} paragraph={false} />
      ) : (
        <div
          style={{
            fontSize: valueSize === "kpi" ? fontSize.kpi : fontSize.sectionTitle,
            fontWeight: fontWeight.semibold,
            lineHeight: 1.3,
            color: toneColor,
            display: "flex",
            alignItems: "baseline",
            gap: space.xs,
          }}
        >
          {prefix}
          <span>{value}</span>
          {suffix && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.body }}>
              {suffix}
            </Typography.Text>
          )}
        </div>
      )}
      {trend && (
        <Space size={space.xs} style={{ fontSize: fontSize.caption, color: trendColor }}>
          {trend.direction === "up" ? <ArrowUpOutlined /> : trend.direction === "down" ? <ArrowDownOutlined /> : null}
          <span>{trend.text}</span>
        </Space>
      )}
      {footer && (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {footer}
        </Typography.Text>
      )}
    </div>
  );
  const card = (
    <Card
      aria-label={ariaLabel}
      style={{
        height: "100%",
        position: "relative",
        borderColor: link && hover ? colors.primary : undefined,
        transition: `border-color ${motion.normal}s`,
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {inner}
      {link && (
        <ArrowRightOutlined
          aria-hidden
          style={{
            position: "absolute",
            top: space.md,
            right: space.md,
            fontSize: iconSize.sm,
            color: hover ? colors.primary : colors.textSecondary,
            transition: `color ${motion.normal}s`,
          }}
        />
      )}
    </Card>
  );
  return link ? link(card) : card;
}
