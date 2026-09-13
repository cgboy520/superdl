/** 充值订单的渠道核验与补单(充值流水行内与异常清单共用):核验结果对话框(订单状态走映射表,渠道原值 Mono)+ 补单原因弹窗。
 *  核验与补单都只给 finance / admin(后端同口径),其余角色由调用方用 GatedButton 灰置。 */

import { adminColors, idemKeyOf, orderStatusMap, space } from "@superdl/ui";
import { useApiErrorText, useFormat } from "@superdl/ui";
import { Mono, StatusTag } from "@superdl/ui/components";
import { App, Form, Input, Modal, Space } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useBackfillOrder, useVerifyOrder } from "../api";
import { canWriteFinance, useAdminRole } from "../stores/auth";

export interface OrderActions {
  /** 渠道核验:结果进对话框;渠道已支付而订单未入账时对话框直接给「补单」 */
  verify: (orderNo: string) => void;
  openBackfill: (orderNo: string) => void;
  /** finance / admin 才可写 */
  writable: boolean;
  verifying: boolean;
  /** 补单弹窗,调用方渲染一次 */
  modals: ReactNode;
}

export function useOrderActions(onDone: () => void): OrderActions {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message, modal } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteFinance(role);
  const verify = useVerifyOrder();
  const backfill = useBackfillOrder();
  const [target, setTarget] = useState<string | null>(null);
  const [form] = Form.useForm<{ reason: string }>();

  const openBackfill = (orderNo: string) => {
    form.resetFields();
    setTarget(orderNo);
  };

  const doVerify = async (orderNo: string) => {
    try {
      const r = await verify.mutateAsync({ orderNo });
      const content = (
        <Space orientation="vertical" size={space.xs}>
          <Space size={space.xs}>
            <span>{t("finance.verifyOrderStatus")}</span>
            <StatusTag map={orderStatusMap} value={r.order_status} />
            <span>{t("finance.verifyOrderAmount", { amount: formatMoney(r.order_amount) })}</span>
          </Space>
          {/* 渠道状态是第三方原值,不进 t() */}
          <Space size={space.xs}>
            <span>{t("finance.verifyChannelStatusLabel")}</span>
            <Mono>{r.channel_status}</Mono>
          </Space>
          <span>
            {t("finance.verifyChannelAmount", { amount: r.channel_amount ? formatMoney(r.channel_amount) : "—" })}
          </span>
          <span>{t("finance.verifyChannelTxn", { id: r.channel_txn_id ?? "—" })}</span>
          <b style={{ color: r.matches ? adminColors.positive : adminColors.negative }}>
            {r.matches ? t("finance.verifyMatch") : t("finance.verifyMismatch")}
          </b>
        </Space>
      );
      const title = t("finance.verifyTitle", { no: orderNo });
      // 渠道已支付而订单未入账:对话框底部直接给补单出口
      if (r.matches && r.order_status !== "paid" && writable) {
        modal.confirm({
          title,
          content,
          okText: t("finance.backfill"),
          cancelText: t("common.close"),
          onOk: () => openBackfill(orderNo),
        });
      } else {
        modal.info({ title, content, okText: t("common.close") });
      }
    } catch (e) {
      message.error(errText(e, t("finance.verifyFailed")));
    }
  };

  const submitBackfill = async () => {
    let v: { reason: string };
    try {
      v = await form.validateFields();
    } catch {
      return; // 校验失败:antd 已就地标红
    }
    if (target === null) return;
    try {
      await backfill.mutateAsync({
        orderNo: target,
        data: { reason: v.reason },
        // 幂等键从快照派生
        idempotencyKey: idemKeyOf("backfill", [target, v.reason]),
      });
      message.success(t("finance.backfilled"));
      setTarget(null);
      form.resetFields();
      onDone();
    } catch (e) {
      message.error(errText(e, t("finance.backfillFailed")));
    }
  };

  const modals = (
    <Modal
      title={t("finance.backfillTitle", { no: target ?? "" })}
      open={target !== null}
      onCancel={() => setTarget(null)}
      okText={t("finance.backfillOk")}
      okButtonProps={{ loading: backfill.isPending }}
      onOk={() => void submitBackfill()}
    >
      <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
        <span style={{ color: adminColors.textSecondary }}>{t("finance.backfillNote")}</span>
        <Form form={form} layout="vertical">
          <Form.Item
            name="reason"
            label={t("common.reasonLabel")}
            rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
          >
            <Input.TextArea rows={2} placeholder={t("finance.backfillReasonPlaceholder")} />
          </Form.Item>
        </Form>
      </Space>
    </Modal>
  );

  return {
    verify: (orderNo: string) => void doVerify(orderNo),
    openBackfill,
    writable,
    verifying: verify.isPending,
    modals,
  };
}
