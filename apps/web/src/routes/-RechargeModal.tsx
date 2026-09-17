/** Recharge modal: channel tiles from `/site-config.payment_channels` (only enabled channels, with the
 *  server-declared presentation), amount presets + input with balance preview, then either a QR code
 *  (`qr` channels) or a hand-off to the provider's checkout page (`redirect` channels). Polls the order
 *  until a terminal status; an unfinished order is resumed from sessionStorage or `/billing?recharge=`. */

import { useTranslation } from "react-i18next";
import { Alert, App, Button, InputNumber, Modal, QRCode, Space, Typography, Spin } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { type PaymentChannelOut, type RechargeOut } from "@superdl/api-client";
import {
  addAmounts,
  compareAmounts,
  controlWidth,
  fontSize,
  formatDateTime,
  idemKeyOf,
  paymentChannelLabelKey,
  space,
} from "@superdl/ui";
import { ChipRow, DataErrorAlert, EMPTY_VALUE, OptionTileGroup } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { keys } from "../api/keys";
import { useCreateRecharge, useMockPay } from "../api/mutations";
import { usePolicies, useRecharge, useSiteConfig, useWallet } from "../api/queries";

/** Order number of the top-up in progress (sessionStorage): closing and reopening resumes the poll. */
export const PENDING_ORDER_KEY = "superdl.web.pendingRecharge";

export function PayCountdown({ expiresAt }: { expiresAt: string }) {
  const { t } = useTranslation();
  const [left, setLeft] = useState(() => Math.max(0, new Date(expiresAt).getTime() - Date.now()));
  useEffect(() => {
    const timer = setInterval(() => setLeft(Math.max(0, new Date(expiresAt).getTime() - Date.now())), 1000);
    return () => clearInterval(timer);
  }, [expiresAt]);
  if (left <= 0) return <span>{t("billing.orderExpired")}</span>;
  const total = Math.floor(left / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  const time = h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
  return <span>{t("billing.payCountdown", { time })}</span>;
}

/** Channel display name: shared status label when known, otherwise the API name. */
function useChannelLabel(): (name: string) => string {
  const { t } = useTranslation();
  const loose = t as unknown as (key: string) => string;
  return (name) => {
    const key = paymentChannelLabelKey(name);
    return key ? loose(key) : name;
  };
}

export function RechargeModal({
  open,
  onClose,
  resumeOrderNo,
}: {
  open: boolean;
  onClose: () => void;
  /** Order to resume (from `/billing?recharge=`); takes precedence over sessionStorage. */
  resumeOrderNo?: string;
}) {
  const { currencySymbol, formatMoney, minorUnits } = useFormat();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const channelLabel = useChannelLabel();
  const policiesQ = usePolicies();
  const { data: policies } = policiesQ;
  // No invented bounds: the form renders only from a successful /policies answer.
  const presets = policies?.recharge_presets ?? [];
  const minAmount = policies?.recharge_min ?? "";
  const maxAmount = policies?.recharge_max ?? "";
  const [amount, setAmount] = useState<string | null>(null);
  const effectiveAmount = amount ?? presets[1] ?? presets[0] ?? minAmount;
  const [order, setOrder] = useState<RechargeOut | null>(null);
  const [orderSeq, setOrderSeq] = useState(0);
  const [pickedChannel, setPickedChannel] = useState<string | null>(null);
  const [resumedNo, setResumedNo] = useState(() => resumeOrderNo ?? sessionStorage.getItem(PENDING_ORDER_KEY) ?? "");
  const { data: wallet } = useWallet();

  const siteQ = useSiteConfig();
  const channels: PaymentChannelOut[] = siteQ.data?.payment_channels ?? [];
  const channel = channels.find((c) => c.name === pickedChannel) ?? channels[0];
  const anyEnabled = channels.length > 0;
  const redirect = channel?.presentation === "redirect";

  const create = useCreateRecharge({
    onSuccess: (d) => {
      const o = d as RechargeOut;
      setOrder(o);
      setResumedNo(o.order_no);
      sessionStorage.setItem(PENDING_ORDER_KEY, o.order_no);
      setOrderSeq((s) => s + 1);
      if (o.presentation === "redirect" && o.payment_url) window.location.assign(o.payment_url);
    },
  });
  const mockPay = useMockPay({
    onSuccess: () => {
      message.success(t("billing.mockPaySent"));
    },
  });
  const activeNo = order?.order_no ?? resumedNo;
  const rechargeQ = useRecharge(activeNo, {
    enabled: open && activeNo !== "",
    refetchInterval: (q) => {
      if (q.state.status === "error") return false;
      return q.state.data && q.state.data.status !== "pending" ? false : 2_000;
    },
  });
  const { data: polled } = rechargeQ;
  useEffect(() => {
    if (rechargeQ.isError && !order) sessionStorage.removeItem(PENDING_ORDER_KEY);
  }, [rechargeQ.isError, order]);
  const shown = polled ?? order;
  const status = shown?.status;
  const paid = status === "paid";

  useEffect(() => {
    if (!status) return;
    if (status === "paid") {
      sessionStorage.removeItem(PENDING_ORDER_KEY);
      void queryClient.invalidateQueries({ queryKey: keys.wallet });
      void queryClient.invalidateQueries({ queryKey: keys.ledger.all });
    } else if (status !== "pending") {
      sessionStorage.removeItem(PENDING_ORDER_KEY);
    }
  }, [status, queryClient]);

  const reset = () => {
    setOrder(null);
    setResumedNo("");
    onClose();
  };
  const backToForm = () => {
    setOrder(null);
    setResumedNo("");
  };

  const presetValue = presets.find((v) => compareAmounts(v, effectiveAmount) === 0) ?? "";
  const balanceAfter = wallet ? formatMoney(addAmounts(wallet.balance, effectiveAmount)) : EMPTY_VALUE;

  return (
    <Modal
      title={t("billing.recharge")}
      open={open}
      onCancel={reset}
      footer={null}
      afterOpenChange={(o) => {
        if (o && !order) setResumedNo(resumeOrderNo ?? sessionStorage.getItem(PENDING_ORDER_KEY) ?? "");
      }}
    >
      {!shown && policiesQ.isPending ? (
        <Spin />
      ) : !shown && policiesQ.isError ? (
        <DataErrorAlert onRetry={() => void policiesQ.refetch()} />
      ) : !shown ? (
        <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
          {siteQ.isError && <DataErrorAlert onRetry={() => void siteQ.refetch()} />}
          {siteQ.isSuccess && !anyEnabled && <Alert type="warning" showIcon title={t("billing.noChannels")} />}
          {anyEnabled && (
            <OptionTileGroup
              label={t("billing.channelLabel")}
              columns={channels.length >= 3 ? 3 : channels.length === 2 ? 2 : 1}
              size="sm"
              value={channel?.name ?? ""}
              onChange={setPickedChannel}
              options={channels.map((c) => ({ value: c.name, title: channelLabel(c.name) }))}
            />
          )}
          {presets.length > 0 && (
            <ChipRow
              label={t("billing.presetAmounts")}
              value={presetValue}
              onChange={(v) => {
                if (v !== "") setAmount(v);
              }}
              options={presets.map((v) => ({ value: v, label: formatMoney(v) }))}
            />
          )}
          <InputNumber
            style={{ width: controlWidth.md }}
            min={minAmount}
            max={maxAmount}
            precision={minorUnits}
            stringMode
            value={effectiveAmount}
            onChange={(v) => setAmount(v ?? minAmount)}
            prefix={currencySymbol}
            aria-label={t("billing.rechargeAmount")}
          />
          <Typography.Text type="secondary">{t("billing.balanceAfter", { amount: balanceAfter })}</Typography.Text>
          {redirect && <Typography.Text type="secondary">{t("billing.redirectNote")}</Typography.Text>}
          <Button
            type="primary"
            block
            disabled={!anyEnabled || !channel}
            loading={create.isPending}
            onClick={() => {
              if (!channel) return;
              create.mutate({
                body: { amount: effectiveAmount, channel: channel.name },
                idempotencyKey: idemKeyOf("recharge", [orderSeq, effectiveAmount, channel.name]),
              });
            }}
          >
            {redirect ? t("billing.goToCheckout") : t("billing.genQr")}
          </Button>
        </Space>
      ) : paid ? (
        <Space orientation="vertical" align="center" style={{ width: "100%" }}>
          <Typography.Title level={4} type="success">
            {t("billing.paySuccess")}
          </Typography.Title>
          <Typography.Text>{t("billing.credited", { amount: formatMoney(shown.amount) })}</Typography.Text>
          <Button type="primary" onClick={reset}>
            {t("billing.done")}
          </Button>
        </Space>
      ) : (
        <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            title={t("billing.orderWaiting", {
              no: shown.order_no,
              time: formatDateTime(shown.expires_at),
            })}
            description={<PayCountdown expiresAt={shown.expires_at} />}
          />
          {shown.presentation === "redirect" ? (
            <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
              <Typography.Text type="secondary">{t("billing.redirectNote")}</Typography.Text>
              {shown.payment_url ? (
                <Button type="primary" block onClick={() => window.location.assign(shown.payment_url ?? "")}>
                  {t("billing.openCheckoutAgain")}
                </Button>
              ) : (
                <Typography.Text type="danger">{t("billing.qrFailed")}</Typography.Text>
              )}
            </Space>
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "center" }}>
                {shown.payment_url ? (
                  <QRCode value={shown.payment_url} size={168} />
                ) : (
                  <Space orientation="vertical" size={space.md} align="center">
                    <Typography.Text type="danger">{t("billing.qrFailed")}</Typography.Text>
                    <Button
                      onClick={() => {
                        sessionStorage.removeItem(PENDING_ORDER_KEY);
                        setOrder(null);
                        setResumedNo("");
                      }}
                    >
                      {t("billing.qrRetry")}
                    </Button>
                  </Space>
                )}
              </div>
              {shown.channel !== "mock" && (
                <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
                  {t("billing.scanToPay", { channel: channelLabel(shown.channel) })}
                </Typography.Text>
              )}
            </>
          )}
          {shown.channel === "mock" && (
            <Button
              block
              loading={mockPay.isPending}
              onClick={() => mockPay.mutate({ order_no: shown.order_no, amount: shown.amount })}
            >
              {t("billing.mockPayNow")}
            </Button>
          )}
          <Space size={space.sm} wrap>
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("billing.pollNote")}
            </Typography.Text>
            <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={backToForm}>
              {t("billing.changeAmount")}
            </Button>
          </Space>
        </Space>
      )}
    </Modal>
  );
}
