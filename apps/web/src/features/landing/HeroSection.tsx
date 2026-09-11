/** 主页 Hero:单帧深靛渐变 + 网格纹理 + 右侧玻璃拟态实时数据卡。数据卡三态:未就绪骨架 / 失败降级为「前往算力市场」CTA / 实时数据;失败时不渲染假 0。 */

import { brand, brandInverseButtonStyle, compareAmounts, fontSize, layout } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Grid, Skeleton, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useFormat } from "@superdl/ui";
import { GRID_TEXTURE } from "../../components/gridTexture";
import { dedupAvailableTotal } from "../../lib/inventory";
import { useSkus } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";

function GlassCard({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "rgba(255,255,255,0.12)",
        border: "1px solid rgba(255,255,255,0.25)",
        borderRadius: 12,
        backdropFilter: "blur(8px)",
        padding: "16px 24px",
        color: "#fff",
        minWidth: 200,
      }}
    >
      <div style={{ fontSize: fontSize.caption, opacity: 0.8 }}>{label}</div>
      <div style={{ fontSize: fontSize.kpi, fontWeight: 700, marginTop: 4 }}>{value}</div>
    </div>
  );
}

export function HeroSection() {
  const { t } = useTranslation();
  const { formatHourlyPrice } = useFormat();
  const loggedIn = useIsLoggedIn();
  const screens = Grid.useBreakpoint();
  const skusQ = useSkus({ refetchInterval: 60_000 });
  const { data: skus } = skusQ;

  const minPrice = (skus ?? []).reduce<string | null>(
    // 金额比较走 compareAmounts(BigInt 万分位)
    (min, s) => (min === null || compareAmounts(s.price_hourly, min) < 0 ? s.price_hourly : min),
    null,
  );
  const freeCards = dedupAvailableTotal(skus ?? []);

  return (
    <section
      style={{
        // 多重背景:网格纹理叠在渐变上
        backgroundImage: `${GRID_TEXTURE}, ${brand.heroBg}`,
        padding: "88px 24px 96px",
      }}
    >
      <div
        style={{
          maxWidth: layout.pageMaxWidthWide,
          margin: "0 auto",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 48,
        }}
      >
        <div>
          <Typography.Title
            style={{ color: "#fff", fontSize: fontSize.kpi, marginBottom: 12, marginTop: 0 }}
          >
            {t("landing.hero.title")}
          </Typography.Title>
          <Typography.Paragraph
            style={{ color: "rgba(255,255,255,0.85)", fontSize: fontSize.pageTitle, marginBottom: 32 }}
          >
            {t("landing.hero.subtitle")}
          </Typography.Paragraph>
          <Space size={16}>
            {/* 未登录主 CTA 直达注册态 */}
            <Link to={loggedIn ? "/instances" : "/login"} search={loggedIn ? {} : { mode: "register" }}>
              <Button size="large" style={{ ...brandInverseButtonStyle, paddingInline: 32 }}>
                {loggedIn ? t("common.enterConsole") : t("landing.hero.ctaPrimary")}
              </Button>
            </Link>
            <Link to="/market">
              <Button size="large" ghost style={{ paddingInline: 24 }}>
                {t("landing.hero.ctaSecondary")}
              </Button>
            </Link>
          </Space>
        </div>
        {screens.lg && (
          <Space orientation="vertical" size={16}>
            {skusQ.isError ? (
              // 实时数据查询失败:整卡降级为市场入口
              <Link to="/market">
                <Button size="large" ghost>
                  {t("landing.pricing.fallbackCta")}
                </Button>
              </Link>
            ) : !skus ? (
              <>
                <Skeleton.Input active style={{ width: 200, height: 88 }} />
                <Skeleton.Input active style={{ width: 200, height: 88 }} />
              </>
            ) : (
              <>
                {minPrice && (
                  <GlassCard
                    label={t("landing.hero.minPriceLabel")}
                    value={formatHourlyPrice(minPrice)}
                  />
                )}
                <GlassCard
                  label={t("landing.hero.freeLabel")}
                  value={t("landing.hero.freeCards", { count: freeCards })}
                />
              </>
            )}
          </Space>
        )}
      </div>
    </section>
  );
}
