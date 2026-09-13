/** 「怎么计费」:三条事实 + 一条计费时间轴(开机 → 运行中计费 → 关机停表 → 释放;数据盘按日通贯)。
 *  取代原来的泛营销四宫格 —— 用户在首页要判断的是「这钱怎么算」,不是「我们有多好」。
 *  时间轴用 SVG 只在 ≥md 出;窄屏换成同内容的文字节拍(SVG 缩到手机宽度字会小到读不了)。 */

import { ClockCircleOutlined, DatabaseOutlined, PauseCircleOutlined } from "@ant-design/icons";
import { fontSize, fontWeight, iconSize, layout, space, useThemeColors } from "@superdl/ui";
import { Grid, theme, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { LandingSection } from "./LandingSection";

function FactCard({ icon, title, desc }: { icon: ReactNode; title: string; desc: string }) {
  const { token } = theme.useToken();
  const colors = useThemeColors();
  return (
    <div
      style={{
        background: token.colorBgContainer,
        border: `1px solid ${token.colorBorderSecondary}`,
        borderRadius: layout.cardRadius,
        padding: `${space.lg}px ${space.xl}px`,
        display: "flex",
        flexDirection: "column",
        gap: space.sm,
      }}
    >
      <span style={{ color: colors.primary, fontSize: iconSize.lg, lineHeight: 1 }}>{icon}</span>
      <Typography.Text style={{ fontWeight: fontWeight.semibold, fontSize: fontSize.sectionTitle }}>
        {title}
      </Typography.Text>
      <Typography.Text type="secondary">{desc}</Typography.Text>
    </div>
  );
}

/** 计费时间轴(viewBox 900×132,随容器缩放);颜色随主题取语义色。 */
function BillingTimeline() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const colors = useThemeColors();
  const label = { fontSize: 13, fill: token.colorTextSecondary } as const;
  const eventLabel = { fontSize: 13, fill: token.colorText, fontWeight: fontWeight.medium } as const;
  const dot = (x: number) => <circle cx={x} cy={44} r={5} fill={colors.primary} />;
  return (
    <svg
      viewBox="0 0 900 132"
      style={{ width: "100%", height: "auto", display: "block" }}
      role="img"
      aria-label={t("landing.billing.timelineAria")}
    >
      {/* 事件名 */}
      <text x={60} y={22} textAnchor="start" {...eventLabel}>
        {t("landing.billing.evStart")}
      </text>
      <text x={560} y={22} textAnchor="middle" {...eventLabel}>
        {t("landing.billing.evStop")}
      </text>
      <text x={840} y={22} textAnchor="end" {...eventLabel}>
        {t("landing.billing.evRelease")}
      </text>
      {/* 运行中:GPU 时费 */}
      <rect x={60} y={39} width={500} height={10} rx={5} fill={colors.primary} />
      {/* 关机后:实例盘保留但不计费 */}
      <line x1={560} y1={44} x2={840} y2={44} stroke={token.colorBorder} strokeWidth={2} strokeDasharray="6 6" />
      {dot(60)}
      {dot(560)}
      {dot(840)}
      <text x={310} y={72} textAnchor="middle" {...label}>
        {t("landing.billing.gpuBar")}
      </text>
      <text x={700} y={72} textAnchor="middle" {...label}>
        {t("landing.billing.afterStop")}
      </text>
      {/* 数据盘:全程按日 */}
      <rect x={60} y={92} width={780} height={6} rx={3} fill={colors.positive} />
      <text x={450} y={120} textAnchor="middle" {...label}>
        {t("landing.billing.diskBar")}
      </text>
    </svg>
  );
}

/** 窄屏的等价内容:同样四个节拍,逐行读。 */
function BillingBeats() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const colors = useThemeColors();
  const beats = [
    { k: "evStart", d: "gpuBar" },
    { k: "evStop", d: "afterStop" },
    { k: "evRelease", d: "" },
  ] as const;
  return (
    <ol style={{ margin: 0, paddingInlineStart: space.lg, display: "flex", flexDirection: "column", gap: space.sm }}>
      {beats.map((b) => (
        <li key={b.k} style={{ color: token.colorText }}>
          <Typography.Text style={{ fontWeight: fontWeight.medium }}>
            {t(`landing.billing.${b.k}` as "landing.billing.evStart")}
          </Typography.Text>
          {b.d && (
            <Typography.Text type="secondary" style={{ marginInlineStart: space.sm }}>
              {t(`landing.billing.${b.d}` as "landing.billing.gpuBar")}
            </Typography.Text>
          )}
        </li>
      ))}
      <li style={{ color: colors.positive }}>
        <Typography.Text style={{ color: colors.positive }}>{t("landing.billing.diskBar")}</Typography.Text>
      </li>
    </ol>
  );
}

export function BillingFacts() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const wide = Grid.useBreakpoint().md;
  return (
    <LandingSection id="billing" title={t("landing.billing.title")} subtitle={t("landing.billing.subtitle")}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: layout.cardGap,
          marginBottom: space.xl,
        }}
      >
        <FactCard
          icon={<ClockCircleOutlined />}
          title={t("landing.billing.factSecond")}
          desc={t("landing.billing.factSecondDesc")}
        />
        <FactCard
          icon={<PauseCircleOutlined />}
          title={t("landing.billing.factStop")}
          desc={t("landing.billing.factStopDesc")}
        />
        <FactCard
          icon={<DatabaseOutlined />}
          title={t("landing.billing.factDisk")}
          desc={t("landing.billing.factDiskDesc")}
        />
      </div>
      <div
        style={{
          background: token.colorBgContainer,
          border: `1px solid ${token.colorBorderSecondary}`,
          borderRadius: layout.cardRadius,
          padding: `${space.lg}px ${space.xl}px`,
        }}
      >
        {wide ? <BillingTimeline /> : <BillingBeats />}
      </div>
    </LandingSection>
  );
}
