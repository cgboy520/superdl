/** 指标卡(两端统一):标题 / 大数(单个文本节点)/ 副行 / 趋势;value 为 undefined 时出骨架;link 包住整卡即可点击深链(hover 描边 + 右上箭头)。 */

import { ArrowUpOutlined, ArrowDownOutlined, ArrowRightOutlined, QuestionCircleOutlined } from "@ant-design/icons";
import { Card, Skeleton, Space, Tooltip, Typography } from "antd";
import { useState, type ReactNode } from "react";

import { useThemeColors } from "../hooks/useThemeColors";
import { fontSize, fontWeight, iconSize, motion, space } from "../tokens";

export interface StatCardProps {
  title: ReactNode;
  /** undefined = 加载中(骨架) */
  value: ReactNode | undefined;
  prefix?: ReactNode;
  suffix?: ReactNode;
  /** 标题旁 ? tooltip */
  hint?: ReactNode;
  trend?: { text: ReactNode; direction: "up" | "down" | "flat" };
  /** 副行(对照 / 分母 / 说明) */
  footer?: ReactNode;
  tone?: "default" | "positive" | "negative" | "warning";
  /** 传入即整卡可点:用调用方的 Link 包住内容 */
  link?: (children: ReactNode) => ReactNode;
  ariaLabel?: string;
  /** 加载失败时替换主体的错误条 */
  error?: ReactNode;
  /** 大数字号:kpi(默认)/ sectionTitle(并列多卡) */
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
