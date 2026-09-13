/** 主页 Hero:左标题 + 两个 CTA,右实时行情板(第一屏就给价格与库存,而不是口号)。
 *  ≥lg 左右分栏;<lg 行情板落到 CTA 下方(整块,不隐藏 —— 手机上更需要先看到价格)。 */

import { brand, brandInverseButtonStyle, fontSize, fontWeight, layout, space } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Grid, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { GRID_TEXTURE } from "../../components/gridTexture";
import { useIsLoggedIn } from "../../stores/auth";
import { PriceBoard } from "./PriceBoard";

export function HeroSection() {
  const { t } = useTranslation();
  const loggedIn = useIsLoggedIn();
  const wide = Grid.useBreakpoint().lg;

  return (
    <section
      style={{
        // 多重背景:网格纹理叠在渐变上
        backgroundImage: `${GRID_TEXTURE}, ${brand.heroBg}`,
        padding: `${space.xxl * 2}px ${layout.contentPadding}px ${space.xxl * 2}px`,
      }}
    >
      <div
        style={{
          maxWidth: layout.pageMaxWidthWide,
          margin: "0 auto",
          display: "flex",
          flexDirection: wide ? "row" : "column",
          alignItems: wide ? "center" : "stretch",
          justifyContent: "space-between",
          gap: space.xxl,
        }}
      >
        <div style={{ minWidth: 0, flex: "1 1 auto" }}>
          <Typography.Title
            style={{
              color: brand.onHero,
              fontSize: wide ? fontSize.display : fontSize.kpi,
              fontWeight: fontWeight.semibold,
              lineHeight: 1.2,
              marginTop: 0,
              marginBottom: space.md,
            }}
          >
            {t("landing.hero.title")}
          </Typography.Title>
          <Typography.Paragraph
            style={{
              color: brand.onHeroMuted,
              fontSize: fontSize.pageTitle,
              maxWidth: 600,
              marginBottom: space.xl,
            }}
          >
            {t("landing.hero.subtitle")}
          </Typography.Paragraph>
          <div style={{ display: "flex", gap: space.lg, flexWrap: "wrap" }}>
            {/* 未登录主 CTA 直达注册态 */}
            <Link to={loggedIn ? "/instances" : "/login"} search={loggedIn ? {} : { mode: "register" }}>
              <Button size="large" style={{ ...brandInverseButtonStyle, paddingInline: space.xxl }}>
                {loggedIn ? t("common.enterConsole") : t("landing.hero.ctaPrimary")}
              </Button>
            </Link>
            <Link to="/market">
              <Button size="large" ghost style={{ paddingInline: space.xl }}>
                {t("landing.hero.ctaSecondary")}
              </Button>
            </Link>
          </div>
        </div>
        <div style={{ flex: wide ? "0 0 auto" : "1 1 auto", minWidth: 0 }}>
          <PriceBoard compact={!wide} />
        </div>
      </div>
    </section>
  );
}
