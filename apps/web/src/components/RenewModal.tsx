/**
 * 「给这台机器买一段周期」的 modal,两种模式共用一套 UI,差别只在起点、标题/说明、提交端点:
 * - `renew`   续费:接在当前周期之后,基准取 `subscription.unit_price`
 * - `subscribe` 按量转包周期:从现在起算,基准取 `instance.price_hourly`(建实例时锁定的 SKU 原价)
 *
 * 明细三个数必须是精确值:基准只能取后端下单时用的那个数,不能拿 SKU 现价也不能从折后时价反推,
 * 本地量化顺序与 pricing.quote_subscription 逐步对齐;成功后用响应里的 quote 出扣款回执。
 * 两种模式都是支付动作:一次性预扣整段周期,中途释放不退款。
 *
 * 幂等键每次打开 modal 生成一个:同一次打开内改周期/数量不换键,关掉重开才是新单。
 * 调用方按需挂载(关掉即卸载),「重开 = 新单」由挂载本身保证,不在 effect 里回填状态。
 */

import type { InstanceOut, RenewOut } from "@superdl/api-client";
import {
  billingUnits,
  BILLING_PERIODS,
  addAmounts,
  compareAmounts,
  formatDateTime,
  isBillingPeriod,
  MAX_PERIOD_COUNT,
  PERIOD_HOURS,
  periodMap,
} from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { App, Button, Descriptions, InputNumber, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useRenewInstance, useSubscribeInstance } from "../api/mutations";
import { useWallet } from "../api/queries";
import { useFormat } from "@superdl/ui";
import { ChipRow } from "./ChipRow";
import {
  discountOff,
  PeriodCountUnit,
  PeriodQuoteRows,
  periodQuoteOf,
  usePeriodDiscounts,
} from "./periodBilling";

export type PeriodPurchaseMode = "renew" | "subscribe";

export function RenewModal({
  instance,
  mode = "renew",
  open,
  onClose,
}: {
  instance: InstanceOut;
  /** renew = 续费(接在当前周期之后);subscribe = 按量转包周期(从现在起算) */
  mode?: PeriodPurchaseMode;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const fmt = useFormat();
  const { formatMoney } = fmt;
  const { message } = App.useApp();
  const isConvert = mode === "subscribe";
  const sub = instance.subscription;
  // 转换没有当前周期,默认给包月(四档里最常买的一档);续费默认跟着当前周期
  const current = isBillingPeriod(sub?.period) ? sub.period : "month";
  const [period, setPeriod] = useState(current);
  const [count, setCount] = useState(1);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  // 「现在」在挂载时定一次:每次重渲染都取一遍会让新到期时间在用户眼皮底下抖
  const [mountedAt] = useState(() => Date.now());
  const { data: wallet } = useWallet();
  const discounts = usePeriodDiscounts();

  const onPaid = (data: RenewOut) => {
    message.success(
      isConvert
        ? t("period.convertOk", { amount: formatMoney(data.quote.amount) })
        : t("period.renewOk", { amount: formatMoney(data.quote.amount) }),
      6,
    );
    onClose();
  };
  // 两个 hook 都要无条件调用(hook 规则),按模式取其一提交
  const renew = useRenewInstance(instance.uuid, { onSuccess: onPaid });
  const subscribe = useSubscribeInstance(instance.uuid, { onSuccess: onPaid });
  const submit = isConvert ? subscribe : renew;

  // 报价基准:转换取建实例时锁定的按量价,续费取下单时的原价快照;
  // 两条都不能是 SKU 现价或折后价(instance.price_hourly 在包周期实例上已被折过)
  const baseHourly = isConvert ? instance.price_hourly : sub?.unit_price;
  const quote =
    isConvert || sub
      ? periodQuoteOf(
          baseHourly,
          { units: billingUnits(instance.gpu_count), period, periodCount: count },
          discounts,
        )
      : undefined;

  // 起点:转换从现在起算;续费必须从老到期时刻接上,老周期过了才从现在算,否则会续出开局就少几天的周期
  const startFrom = isConvert
    ? mountedAt
    : Math.max(sub ? new Date(sub.expires_at).getTime() : mountedAt, mountedAt);
  const newExpiry = new Date(startFrom + PERIOD_HOURS[period] * count * 3_600_000).toISOString();

  const balance = wallet?.balance ?? null;
  const afterBalance = quote ? addAmounts(balance, `-${quote.amount}`) : null;
  const enough = quote != null && balance != null && compareAmounts(balance, quote.amount) >= 0;

  return (
    <Modal
      open={open}
      title={
        isConvert
          ? t("period.convertTitle", { name: instance.name })
          : t("period.renewTitle", { name: instance.name })
      }
      onCancel={onClose}
      width={560}
      footer={
        <Space>
          <Button onClick={onClose}>{t("instances.actions.cancel")}</Button>
          {quote != null && balance != null && !enough ? (
            // 余额不足不做死按钮:指到费用中心的可点链接(与创建页结算条同款)
            <Link to="/billing">
              <Button type="primary" danger onClick={onClose}>
                {t("create.notEnoughGoRecharge")}
              </Button>
            </Link>
          ) : (
            <Button
              type="primary"
              disabled={!enough}
              loading={submit.isPending}
              onClick={() =>
                submit.mutate({ body: { period, period_count: count }, idempotencyKey })
              }
            >
              {isConvert ? t("period.convertConfirm") : t("period.renewConfirm")}
            </Button>
          )}
        </Space>
      }
    >
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Descriptions
          size="small"
          column={1}
          items={[
            isConvert
              ? {
                  label: t("period.currentBilling"),
                  // 转换前是按量,时价就是建实例时锁定的那个数(也正是下面的报价基准)
                  children: t("instances.pricePerCard", {
                    price: fmt.formatHourlyPrice(instance.price_hourly),
                    count: instance.gpu_count,
                  }),
                }
              : {
                  label: t("period.currentPeriod"),
                  children: sub
                    ? t("period.currentPeriodRange", {
                        from: formatDateTime(sub.started_at),
                        to: formatDateTime(sub.expires_at),
                      })
                    : "—",
                },
          ]}
        />

        <ChipRow
          label={isConvert ? t("period.convertLength") : t("period.renewLength")}
          value={period}
          onChange={setPeriod}
          options={BILLING_PERIODS.map((p) => {
            const off = discounts ? discountOff(discounts[p]) : 0;
            return {
              value: p,
              label: (
                <span>
                  {t(periodMap[p].labelKey)}
                  {off > 0 && (
                    <Typography.Text type="secondary" style={{ marginInlineStart: 4 }}>
                      {t("period.offPct", { off })}
                    </Typography.Text>
                  )}
                </span>
              ),
            };
          })}
          extra={
            <Space size={8}>
              <InputNumber
                min={1}
                max={MAX_PERIOD_COUNT}
                value={count}
                aria-label={t("period.countLabel")}
                onChange={(v) => setCount(typeof v === "number" ? v : 1)}
                style={{ width: 96 }}
              />
              <PeriodCountUnit period={period} />
            </Space>
          }
        />

        {quote ? (
          <PeriodQuoteRows
            quote={quote}
            gpuCount={instance.gpu_count}
            cpu={instance.gpu_count === 0}
          />
        ) : (
          <Typography.Text type="secondary">{t("period.quotePending")}</Typography.Text>
        )}

        <Descriptions
          size="small"
          column={1}
          items={[
            {
              label: t("common.balance"),
              children: quote
                ? t("period.balanceChange", {
                    before: formatMoney(balance),
                    after: formatMoney(afterBalance),
                  })
                : formatMoney(balance),
            },
            {
              label: t("period.newExpiry"),
              children: formatDateTime(newExpiry),
            },
            {
              label: t("period.startsFrom"),
              children: isConvert ? t("period.startsNow") : t("period.startsAfterCurrent"),
            },
          ]}
        />

        {isConvert && <Typography.Text type="secondary">{t("copy.periodConvertSettles")}</Typography.Text>}
        <Typography.Text type="secondary">{t("copy.periodPrepaid")}</Typography.Text>
        <Typography.Text type="secondary">{t("copy.periodReserved")}</Typography.Text>
      </Space>
    </Modal>
  );
}
