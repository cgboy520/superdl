/** 充值弹窗:渠道 tile / 金额(档位 chip 写入数字框 + 充值后余额预览)/ 二维码 + 到期倒计时 + 轮询自动确认;未完成订单本地续接,二维码态可回表单改金额。 */

import { useTranslation } from "react-i18next";
import { Alert, App, Button, InputNumber, Modal, QRCode, Space, Typography } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { type RechargeOut } from "@superdl/api-client";
import { addAmounts, compareAmounts, controlWidth, fontSize, formatDateTime, idemKeyOf, space } from "@superdl/ui";
import { ChipRow, DataErrorAlert, EMPTY_VALUE, OptionTileGroup } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { keys } from "../api/keys";
import { useCreateRecharge, useMockPay } from "../api/mutations";
import { useRecharge, useSiteConfig, useWallet } from "../api/queries";

/** 充值档位与单笔限额。 */
export const PRESET_AMOUNTS = ["50.00", "100.00", "500.00"] as const;
export const RECHARGE_MIN_AMOUNT = "1";
export const RECHARGE_MAX_AMOUNT = "50000";

/** 进行中的充值订单号(sessionStorage):关窗重开可恢复轮询。 */
export const PENDING_ORDER_KEY = "superdl.web.pendingRecharge";

type Channel = "wechat" | "alipay" | "mock";

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

export function RechargeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { currencySymbol, formatMoney, minorUnits } = useFormat();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState("100.00");
  const [order, setOrder] = useState<RechargeOut | null>(null);
  const [orderSeq, setOrderSeq] = useState(0);
  const [pickedChannel, setPickedChannel] = useState<Channel | null>(null);
  const [resumedNo, setResumedNo] = useState(() => sessionStorage.getItem(PENDING_ORDER_KEY) ?? "");
  const { data: wallet } = useWallet();

  const siteQ = useSiteConfig();
  const { data: site } = siteQ;
  const enabled = {
    wechat: site?.payment_channels.wechat ?? false,
    alipay: site?.payment_channels.alipay ?? false,
    mock: site?.payment_channels.mock ?? false,
  };
  const firstEnabled: Channel = enabled.wechat ? "wechat" : enabled.alipay ? "alipay" : "mock";
  const channel = pickedChannel ?? firstEnabled;
  const anyEnabled = enabled.wechat || enabled.alipay || enabled.mock;
  const channelTip = enabled.mock ? t("copy.channelComingSoon") : t("copy.channelPending");

  const create = useCreateRecharge({
    onSuccess: (d) => {
      const o = d as RechargeOut;
      setOrder(o);
      setResumedNo(o.order_no);
      sessionStorage.setItem(PENDING_ORDER_KEY, o.order_no);
      setOrderSeq((s) => s + 1);
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

  const presetValue = PRESET_AMOUNTS.find((v) => compareAmounts(v, amount) === 0) ?? "";
  const balanceAfter = wallet ? formatMoney(addAmounts(wallet.balance, amount)) : EMPTY_VALUE;

  return (
    <Modal
      title={t("billing.recharge")}
      open={open}
      onCancel={reset}
      footer={null}
      afterOpenChange={(o) => {
        if (o && !order) setResumedNo(sessionStorage.getItem(PENDING_ORDER_KEY) ?? "");
      }}
    >
      {!shown ? (
        <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
          {siteQ.isError && <DataErrorAlert onRetry={() => void siteQ.refetch()} />}
          <OptionTileGroup
            label={t("billing.channelLabel")}
            columns={3}
            size="sm"
            value={channel}
            onChange={setPickedChannel}
            options={[
              { value: "wechat", title: t("billing.wechat"), reason: enabled.wechat ? undefined : channelTip },
              { value: "alipay", title: t("billing.alipay"), reason: enabled.alipay ? undefined : channelTip },
              ...(enabled.mock ? [{ value: "mock" as const, title: t("billing.mockChannel") }] : []),
            ]}
          />
          <ChipRow
            label={t("billing.presetAmounts")}
            value={presetValue}
            onChange={(v) => {
              if (v !== "") setAmount(v);
            }}
            options={PRESET_AMOUNTS.map((v) => ({ value: v, label: formatMoney(v) }))}
          />
          <InputNumber
            style={{ width: controlWidth.md }}
            min={RECHARGE_MIN_AMOUNT}
            max={RECHARGE_MAX_AMOUNT}
            precision={minorUnits}
            stringMode
            value={amount}
            onChange={(v) => setAmount(v ?? "0")}
            prefix={currencySymbol}
            aria-label={t("billing.rechargeAmount")}
          />
          <Typography.Text type="secondary">{t("billing.balanceAfter", { amount: balanceAfter })}</Typography.Text>
          <Button
            type="primary"
            block
            disabled={!anyEnabled}
            loading={create.isPending}
            onClick={() =>
              create.mutate({
                body: { amount, channel },
                idempotencyKey: idemKeyOf("recharge", [orderSeq, amount, channel]),
              })
            }
          >
            {t("billing.genQr")}
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
          <div style={{ display: "flex", justifyContent: "center" }}>
            {shown.qr_url ? (
              <QRCode value={shown.qr_url} size={168} />
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
          {shown.channel === "wechat" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              {t("billing.scanWithWechat")}
            </Typography.Text>
          )}
          {shown.channel === "alipay" && (
            <Typography.Text type="secondary" style={{ display: "block", textAlign: "center" }}>
              {t("billing.scanWithAlipay")}
            </Typography.Text>
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
