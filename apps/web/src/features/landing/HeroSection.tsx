/**
 * 主页 Hero:单帧深靛渐变 + 网格纹理 + 右侧玻璃拟态实时数据卡。
 * 不做轮播 —— 单产品无第二帧内容,空轮播反显模板气(ui-ux-spec §3.0)。
 */

import { brand, colorPrimary, formatHourlyPrice, marketing } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Button, Grid, Space, Typography } from "antd";

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
  const loggedIn = useIsLoggedIn();
  const screens = Grid.useBreakpoint();
  const { data: skus } = useSkus({}, { refetchInterval: 60_000 });

  const minPrice = (skus ?? []).reduce<string | null>(
    (min, s) => (min === null || Number(s.price_hourly) < Number(min) ? s.price_hourly : min),
    null,
  );
  const freeCards = (skus ?? []).reduce((sum, s) => sum + (s.available_count ?? 0), 0);

  return (
    <section
      style={{
        // 多重背景:网格纹理叠在渐变上(渐变即 background-image,不能被单独的 backgroundImage 覆盖)
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
            {marketing.hero.title}
          </Typography.Title>
          <Typography.Paragraph
            style={{ color: "rgba(255,255,255,0.85)", fontSize: 18, marginBottom: 32 }}
          >
            {marketing.hero.subtitle}
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
                {loggedIn ? "进入控制台" : marketing.hero.ctaPrimary}
              </Button>
            </Link>
            <Link to="/market">
              <Button size="large" ghost style={{ paddingInline: 24 }}>
                {marketing.hero.ctaSecondary}
              </Button>
            </Link>
          </Space>
        </div>
        {screens.lg && (
          <Space orientation="vertical" size={16}>
            {minPrice && <GlassCard label="GPU 时价低至" value={formatHourlyPrice(minPrice)} />}
            <GlassCard label="当前空闲可租" value={`${freeCards} 卡`} />
          </Space>
        )}
      </div>
    </section>
  );
}
