/** 公开主页:Hero / 快捷入口 / GPU 价格墙 / 算力排名 / CTA 横幅 / 三栏页脚。/#pricing、/#ranking 锚点挂载后 scrollIntoView。 */

import { brand, webTheme } from "@superdl/ui";
import { createFileRoute } from "@tanstack/react-router";
import { ConfigProvider } from "antd";

import { AppTopBar } from "../components/layout/AppTopBar";
import { SiteFooter } from "../components/layout/SiteFooter";
import { CtaBanner } from "../features/landing/CtaBanner";
import { GpuRankSection } from "../features/landing/GpuRankSection";
import { HeroSection } from "../features/landing/HeroSection";
import { PricingSection } from "../features/landing/PricingSection";
import { QuickEntrySection } from "../features/landing/QuickEntrySection";
import { useHashScroll } from "../lib/useHashScroll";

export const Route = createFileRoute("/")({
  component: LandingPage,
});

function LandingPage() {
  useHashScroll();
  // 落地页整体锁浅色(控制台正常响应主题)
  return (
    <ConfigProvider theme={webTheme}>
      <div style={{ minHeight: "100vh", background: brand.pageBg }}>
        <AppTopBar variant="public" />
        <HeroSection />
        <QuickEntrySection />
        <PricingSection />
        <GpuRankSection />
        <CtaBanner />
        <SiteFooter />
      </div>
    </ConfigProvider>
  );
}
