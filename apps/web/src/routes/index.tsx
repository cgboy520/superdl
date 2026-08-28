/** 公开主页(未登录默认落地页;已登录顶栏换「进入控制台」)。
 *  分区:Hero / 快捷入口 / GPU 价格墙 / 算力排名 / CTA 横幅 / 三栏页脚。 */

import { brand } from "@superdl/ui";
import { createFileRoute } from "@tanstack/react-router";

import { AppTopBar } from "../components/layout/AppTopBar";
import { SiteFooter } from "../components/layout/SiteFooter";
import { CtaBanner } from "../features/landing/CtaBanner";
import { GpuRankSection } from "../features/landing/GpuRankSection";
import { HeroSection } from "../features/landing/HeroSection";
import { PricingSection } from "../features/landing/PricingSection";
import { QuickEntrySection } from "../features/landing/QuickEntrySection";

export const Route = createFileRoute("/")({
  component: LandingPage,
});

function LandingPage() {
  return (
    <div style={{ minHeight: "100vh", background: brand.pageBg }}>
      <AppTopBar variant="public" />
      <HeroSection />
      <QuickEntrySection />
      <PricingSection />
      <GpuRankSection />
      <CtaBanner />
      <SiteFooter />
    </div>
  );
}
