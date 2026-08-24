/** 主页 Hero:单帧深靛渐变 + 网格纹理 + 右侧玻璃拟态实时数据卡。 */

import { brand, colorPrimary } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Grid, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useFormat } from "../../lib/format";
import { dedupAvailableTotal } from "../../lib/inventory";
import { useSkus } from "../../api/queries";
import { useIsLoggedIn } from "../../stores/auth";

const GRID_TEXTURE = `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'><path d='M40 0H0v40' fill='none' stroke='rgba(255,255,255,0.07)'/></svg>`,
)}")`;

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
      <div style={{ fontSize: 13, opacity: 0.8 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, marginTop: 4 }}>{value}</div>
    </div>
  );
}

export function HeroSection() {
  const { t } = useTranslation();
  const { formatHourlyPrice } = useFormat();
  const loggedIn = useIsLoggedIn();
  const screens = Grid.useBreakpoint();
  const { data: skus } = useSkus({}, { refetchInterval: 60_000 });

  const minPrice = (skus ?? []).reduce<string | null>(
    (min, s) => (min === null || Number(s.price_hourly) < Number(min) ? s.price_hourly : min),
    null,
  );
  const freeCards = dedupAvailableTotal(skus ?? []);

  return (
    <section
      style={{
        // 多重背景:网格纹理叠在渐变上(渐变即 background-image)
        backgroundImage: `${GRID_TEXTURE}, ${brand.heroBg}`,
        padding: "88px 24px 96px",
      }}
    >
      <div
        style={{
          maxWidth: 1200,
          margin: "0 auto",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 48,
        }}
      >
        <div>
          <Typography.Title
            style={{ color: "#fff", fontSize: 44, marginBottom: 12, marginTop: 0 }}
          >
            {t("landing.hero.title")}
          </Typography.Title>
          <Typography.Paragraph
            style={{ color: "rgba(255,255,255,0.85)", fontSize: 18, marginBottom: 32 }}
          >
            {t("landing.hero.subtitle")}
          </Typography.Paragraph>
          <Space size={16}>
            <Link to={loggedIn ? "/instances" : "/login"}>
              <Button
                size="large"
                style={{
                  background: "#fff",
                  color: colorPrimary,
                  borderColor: "transparent",
                  fontWeight: 600,
                  paddingInline: 32,
                }}
              >
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
            {minPrice && <GlassCard label={t("landing.hero.minPriceLabel")} value={formatHourlyPrice(minPrice)} />}
            <GlassCard label={t("landing.hero.freeLabel")} value={t("landing.hero.freeCards", { count: freeCards })} />
          </Space>
        )}
      </div>
    </section>
  );
}
