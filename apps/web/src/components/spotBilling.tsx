/**
 * 竞价(spot)共用件:策略读取、折后时价、折扣角标与知情同意 modal。
 * 市场页、创建页、实例列表/详情四处共用 —— 折扣与宽限窗各写一份,迟早出现
 * 「页面写 4 折、实际按 5 折出账」这类没人能复现的差异(与 periodBilling 同一条纪律)。
 *
 * **两个数一律从 `/policies` 读**:运营在管理端随时可调,知情同意里承诺的
 * 「低至 X 折」「提前 N 秒通知」必须与后端真正执行的值是同一个来源。
 */

import { mulPrice, spotHourlyPrice } from "@superdl/ui";
import { Space, Typography } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { usePolicies } from "../api/queries";
import { useFormat } from "../lib/format";
import { ConsentModal } from "./ConsentModal";

export interface SpotPolicy {
  /** 竞价价占按量价的百分数(40 = 4 折) */
  discountPct: number;
  /** 抢占通知到真删 Pod 的宽限窗(秒) */
  graceSeconds: number;
}

/** 竞价策略;policies 未就绪时返回 undefined,调用方一律灰置竞价档而不是按默认值猜。 */
export function useSpotPolicy(): SpotPolicy | undefined {
  const { data: policies } = usePolicies();
  return useMemo(
    () =>
      policies
        ? { discountPct: policies.spot_discount_pct, graceSeconds: policies.spot_grace_seconds }
        : undefined,
    [policies],
  );
}

/** 竞价时价(单份);策略未就绪返回 undefined —— 宁可不显示,也不按硬编码的折扣算一个假数。 */
export function spotPriceOf(
  baseHourly: string | null | undefined,
  policy: SpotPolicy | undefined,
): string | undefined {
  return policy ? spotHourlyPrice(baseHourly, policy.discountPct) : undefined;
}

/**
 * 折后时价 + 原价划线。`units` 是「一小时收几份」(GPU 卡数;CPU 规格恒 1),
 * 与结算条其余价格同口径。
 */
export function SpotPriceInline({
  baseHourly,
  units,
  policy,
}: {
  baseHourly: string | null | undefined;
  units: number;
  policy: SpotPolicy | undefined;
}) {
  const { formatHourlyPrice } = useFormat();
  const spot = spotPriceOf(baseHourly, policy);
  if (!spot) return <>{formatHourlyPrice(mulPrice(baseHourly, units))}</>;
  return (
    <Space size={8} align="baseline">
      <Typography.Text type="secondary" delete style={{ fontSize: 14 }}>
        {formatHourlyPrice(mulPrice(baseHourly, units))}
      </Typography.Text>
      <span>{formatHourlyPrice(mulPrice(spot, units))}</span>
    </Space>
  );
}

/** 「低至 4 折」角标(zh)/「as low as 40% of on-demand」(en);折数由 packages/ui 按语言给。 */
export function SpotOffLabel({ policy }: { policy: SpotPolicy | undefined }) {
  const { t } = useTranslation();
  const { formatSpotDiscount } = useFormat();
  if (!policy) return null;
  return (
    <Typography.Text type="secondary" style={{ marginInlineStart: 4 }}>
      {t("market.spotOff", { discount: formatSpotDiscount(policy.discountPct) })}
    </Typography.Text>
  );
}

/**
 * 竞价知情同意(创建页提交前弹,与经济档同一套 ConsentModal 形态)。
 * 五条逐字对应后端真正的行为:折扣、回收顺序、宽限通知、保留实例盘并按秒结算、可自行开机。
 * 策略未就绪时不渲染 —— 承诺里带着空数字的同意书没有意义。
 */
export function SpotConsentModal({
  open,
  policy,
  loading,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  policy: SpotPolicy | undefined;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  if (!policy) return null;
  return (
    <ConsentModal
      open={open}
      title={t("create.spotModalTitle")}
      lines={[
        t("copy.spotConsent.c1", { pct: policy.discountPct }),
        t("copy.spotConsent.c2"),
        t("copy.spotConsent.c3", { seconds: policy.graceSeconds }),
        t("copy.spotConsent.c4"),
        t("copy.spotConsent.c5"),
      ]}
      agreeLabel={t("create.spotAgree")}
      confirmLabel={t("create.spotConfirm")}
      loading={loading}
      onCancel={onCancel}
      onConfirm={onConfirm}
    />
  );
}
