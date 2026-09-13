/** 「三步开机」:充值 → 选规格 → SSH 连接,每步带真实入口;第三步给真实命令形状(占位符与帮助页、实例详情同一口径,不编造主机名)。
 *  编号是真序列(必须按序做),不是装饰。 */

import { brand, fontFamilyMono, fontSize, fontWeight, layout, space, textOnAccent } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { theme, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useIsLoggedIn } from "../../stores/auth";
import { LandingSection } from "./LandingSection";

function StepCard({ n, title, desc, children }: { n: number; title: string; desc: string; children?: ReactNode }) {
  const { token } = theme.useToken();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: space.sm }}>
      <span
        style={{
          fontFamily: fontFamilyMono,
          fontSize: fontSize.kpi,
          fontWeight: fontWeight.semibold,
          lineHeight: 1,
          color: token.colorTextQuaternary,
        }}
      >
        {n}
      </span>
      <Typography.Text style={{ fontWeight: fontWeight.semibold, fontSize: fontSize.sectionTitle }}>
        {title}
      </Typography.Text>
      <Typography.Text type="secondary">{desc}</Typography.Text>
      {children}
    </div>
  );
}

export function QuickStartSection() {
  const { t } = useTranslation();
  const loggedIn = useIsLoggedIn();

  return (
    <LandingSection id="quickstart" title={t("landing.quickStart.title")} subtitle={t("landing.quickStart.subtitle")}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: layout.cardGap,
          alignItems: "start",
        }}
      >
        <StepCard n={1} title={t("landing.quickStart.step1")} desc={t("landing.quickStart.step1Desc")}>
          {/* 已登录直接去费用中心充值,未登录先注册 */}
          <Link to={loggedIn ? "/billing" : "/login"} search={loggedIn ? {} : { mode: "register" }}>
            {loggedIn ? t("landing.quickStart.step1LinkIn") : t("landing.quickStart.step1Link")}
          </Link>
        </StepCard>
        <StepCard n={2} title={t("landing.quickStart.step2")} desc={t("landing.quickStart.step2Desc")}>
          <Link to="/market">{t("landing.quickStart.step2Link")}</Link>
        </StepCard>
        <StepCard n={3} title={t("landing.quickStart.step3")} desc={t("landing.quickStart.step3Desc")}>
          <div
            style={{
              background: brand.ink,
              border: `1px solid ${brand.inkBorder}`,
              borderRadius: layout.cardRadius,
              padding: `${space.md}px ${space.lg}px`,
              fontFamily: fontFamilyMono,
              color: textOnAccent,
              overflowX: "auto",
            }}
          >
            <span style={{ color: brand.inkTextMuted, userSelect: "none" }}>$ </span>
            {t("landing.quickStart.command")}
          </div>
        </StepCard>
      </div>
    </LandingSection>
  );
}
