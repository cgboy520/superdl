/** Public home: prices, billing explainer, GPU prices and ranking, start guide and CTA. */

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
