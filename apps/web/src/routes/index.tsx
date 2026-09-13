/** 公开主页:Hero(行情板)/ 计费三事实 / GPU 价格墙 / 算力排名 / 三步开机 / CTA 横幅 / 三栏页脚。
 *  整页跟随主题(顶栏主题钮在公开层也生效);/#pricing、/#ranking、/#quickstart 锚点挂载后 scrollIntoView。 */

import { createFileRoute } from "@tanstack/react-router";
import { theme } from "antd";

import { AppTopBar } from "../components/layout/AppTopBar";
import { SiteFooter } from "../components/layout/SiteFooter";
import { BillingFacts } from "../features/landing/BillingFacts";
import { CtaBanner } from "../features/landing/CtaBanner";
import { GpuRankSection } from "../features/landing/GpuRankSection";
import { HeroSection } from "../features/landing/HeroSection";
import { PricingSection } from "../features/landing/PricingSection";
import { QuickStartSection } from "../features/landing/QuickStartSection";
import { useHashScroll } from "../lib/useHashScroll";

export const Route = createFileRoute("/")({
  component: LandingPage,
});

function LandingPage() {
  useHashScroll();
  const { token } = theme.useToken();
  return (
    <div style={{ minHeight: "100vh", background: token.colorBgLayout }}>
      <AppTopBar variant="public" />
      <main id="main">
        <HeroSection />
        <BillingFacts />
        <PricingSection />
        <GpuRankSection />
        <QuickStartSection />
        <CtaBanner />
      </main>
      <SiteFooter />
    </div>
  );
}
