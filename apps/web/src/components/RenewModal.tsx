/** Renewal / convert-to-subscription modal: local quote, balance check and charge receipt; the idempotency key is generated on mount. */

import type { InstanceOut, RenewOut } from "@superdl/api-client";
import {
  addAmounts,
  BILLING_PERIODS,
  billingUnits,
  compareAmounts,
  formatDateTime,
  isBillingPeriod,
  MAX_PERIOD_COUNT,
  PERIOD_HOURS,
  periodMap,
  space,
} from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { App, Button, InputNumber, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useRenewInstance, useSubscribeInstance } from "../api/mutations";
import { useWallet } from "../api/queries";
import { useFormat } from "@superdl/ui";
import { ChipRow, KeyValue } from "@superdl/ui/components";
import { discountOff, PeriodCountUnit, PeriodQuoteRows, periodQuoteOf, usePeriodDiscounts } from "./periodBilling";

type PeriodPurchaseMode = "renew" | "subscribe";

export function RenewModal({
  instance,
  mode = "renew",
  open,
  onClose,
}: {
  instance: InstanceOut;
  /** renew continues from the later of the expiry and the mount time; subscribe counts from the mount time. */
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
  const current = isBillingPeriod(sub?.period) ? sub.period : "month";
  const [period, setPeriod] = useState(current);
  const [count, setCount] = useState(1);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
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
  const renew = useRenewInstance(instance.uuid, { onSuccess: onPaid });
  const subscribe = useSubscribeInstance(instance.uuid, { onSuccess: onPaid });
  const submit = isConvert ? subscribe : renew;

  const baseHourly = isConvert ? instance.price_hourly : (sub?.unit_price ?? null);
  const quote =
    baseHourly !== null
      ? periodQuoteOf(baseHourly, { units: billingUnits(instance.gpu_count), period, periodCount: count }, discounts)
      : undefined;

  const startFrom = isConvert ? mountedAt : Math.max(sub ? new Date(sub.expires_at).getTime() : mountedAt, mountedAt);
  const newExpiry = new Date(startFrom + PERIOD_HOURS[period] * count * 3_600_000).toISOString();

  const balance = wallet?.balance ?? null;
  const afterBalance = quote && balance !== null ? addAmounts(balance, `-${quote.amount}`) : null;
  const enough = quote != null && balance != null && compareAmounts(balance, quote.amount) >= 0;

  return (
    <Modal
      open={open}
      title={
        isConvert ? t("period.convertTitle", { name: instance.name }) : t("period.renewTitle", { name: instance.name })
      }
      onCancel={onClose}
      width={560}
      footer={
        <Space>
          <Button onClick={onClose}>{t("instances.actions.cancel")}</Button>
          {quote != null && balance != null && !enough ? (
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
              onClick={() => submit.mutate({ body: { period, period_count: count }, idempotencyKey })}
            >
              {isConvert ? t("period.convertConfirm") : t("period.renewConfirm")}
            </Button>
          )}
        </Space>
      }
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <KeyValue
          items={[
            isConvert
              ? {
                  label: t("period.currentBilling"),
                  value: t("instances.pricePerCard", {
                    price: fmt.formatHourlyPrice(instance.price_hourly),
                    count: instance.gpu_count,
                  }),
                }
              : {
                  label: t("period.currentPeriod"),
                  value: sub
                    ? t("period.currentPeriodRange", {
                        from: formatDateTime(sub.started_at),
                        to: formatDateTime(sub.expires_at),
                      })
                    : null,
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
            <Space size={space.sm}>
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
          <PeriodQuoteRows quote={quote} gpuCount={instance.gpu_count} cpu={instance.gpu_count === 0} />
        ) : (
          <Typography.Text type="secondary">{t("period.quotePending")}</Typography.Text>
        )}

        <KeyValue
          items={[
            {
              label: t("common.balance"),
              value:
                afterBalance !== null && balance !== null
                  ? t("period.balanceChange", {
                      before: formatMoney(balance),
                      after: formatMoney(afterBalance),
                    })
                  : balance !== null
                    ? formatMoney(balance)
                    : null,
            },
            {
              label: t("period.newExpiry"),
              value: formatDateTime(newExpiry),
            },
            {
              label: t("period.startsFrom"),
              value: isConvert ? t("period.startsNow") : t("period.startsAfterCurrent"),
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
