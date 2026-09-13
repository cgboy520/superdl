/** 新手引导三步(充值 → 选规格 → 开机):只在实例列表真空态出现;有已支付充值时第 1 步置完成。 */

import { Link } from "@tanstack/react-router";
import { Button, Space, Steps, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useRefundableOrders } from "../api/queries";
import { space } from "@superdl/ui";

export function OnboardingSteps() {
  const { t } = useTranslation();
  const { data: orders } = useRefundableOrders();
  const hasPaid = (orders?.length ?? 0) > 0;
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%", maxWidth: 720 }}>
      <Typography.Text strong>{t("onboarding.title")}</Typography.Text>
      <Steps
        size="small"
        current={hasPaid ? 1 : 0}
        items={[
          {
            title: t("onboarding.step1"),
            description: hasPaid ? t("onboarding.step1Done") : <Link to="/billing">{t("onboarding.step1Action")}</Link>,
          },
          {
            title: t("onboarding.step2"),
            description: <Link to="/market">{t("onboarding.step2Action")}</Link>,
          },
          { title: t("onboarding.step3"), description: t("onboarding.step3Desc") },
        ]}
      />
      <Link to="/market">
        <Button type="primary">{t("instances.goMarket")}</Button>
      </Link>
    </Space>
  );
}
