/** 原因必填的操作弹窗;confirm=false 时跳过二次确认。 */

import { fontSize, space } from "@superdl/ui";
import { App, Form, Input, Modal, Space, Typography } from "antd";
import type { ButtonProps } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useApiErrorText } from "@superdl/ui";
import { GatedButton } from "@superdl/ui/components";

import { REASON_MAX_LEN } from "../lib/validators";

interface Props {
  label: string;
  title: string;
  confirmText: string;
  /** 目标标识(两步弹窗顶部回显) */
  target?: ReactNode;
  /** 确认按钮标红。 */
  danger?: boolean;
  /** 触发按钮标红,默认跟随 danger。 */
  triggerDanger?: boolean;
  disabled?: boolean;
  /** 禁用原因(tooltip) */
  disabledReason: string;
  /** 触发按钮尺寸(默认 small) */
  size?: ButtonProps["size"];
  /** 触发按钮形态(行内「更多」里用 link) */
  type?: ButtonProps["type"];
  /** 二次确认(默认开);恢复方向动作传 false,只填原因 */
  confirm?: boolean;
  /** 返回非空字符串作成功提示,否则用通用文案。 */
  onSubmit: (reason: string) => Promise<string> | Promise<void>;
}

export function ReasonAction({
  label,
  title,
  confirmText,
  target,
  danger,
  triggerDanger = danger,
  disabled,
  disabledReason,
  size = "small",
  type,
  confirm = true,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reasonSnapshot, setReasonSnapshot] = useState("");
  const [form] = Form.useForm<{ reason: string }>();

  const button = (
    <GatedButton
      reason={disabled ? disabledReason : undefined}
      danger={triggerDanger}
      size={size}
      type={type}
      onClick={() => setOpen(true)}
    >
      {label}
    </GatedButton>
  );

  const run = async (reason: string) => {
    setLoading(true);
    try {
      const custom = await onSubmit(reason);
      message.success(custom || t("common.actionDone", { action: title }));
      setOpen(false);
      setConfirming(false);
      form.resetFields();
    } catch (e) {
      message.error(errText(e, t("common.actionFailed", { action: title })));
    } finally {
      setLoading(false);
    }
  };

  const targetLine = target ? (
    <Typography.Text type="secondary" style={{ display: "block", fontSize: fontSize.caption, marginBottom: space.sm }}>
      {t("common.targetLabel")}:{target}
    </Typography.Text>
  ) : null;

  const goNext = async () => {
    try {
      await form.validateFields();
    } catch {
      return;
    }
    const reason = form.getFieldValue("reason") as string;
    setReasonSnapshot(reason);
    if (!confirm) {
      await run(reason);
      return;
    }
    setOpen(false);
    setConfirming(true);
  };

  return (
    <>
      {button}
      <Modal
        title={title}
        open={open}
        onCancel={() => {
          if (loading) return;
          setOpen(false);
          setConfirming(false);
        }}
        mask={{ closable: confirm || !loading }}
        keyboard={confirm || !loading}
        onOk={() => void goNext()}
        okText={confirm ? t("common.next") : t("common.confirmExecute")}
        okButtonProps={confirm ? undefined : { danger, loading }}
        destroyOnHidden
      >
        {targetLine}
        <Form form={form} layout="vertical">
          <Form.Item
            name="reason"
            label={t("common.reasonLabel")}
            rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
          >
            <Input.TextArea
              rows={3}
              maxLength={REASON_MAX_LEN}
              showCount
              placeholder={t("common.reasonPlaceholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  void goNext();
                }
              }}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={t("common.secondConfirm")}
        open={confirming}
        mask={{ closable: !loading }}
        keyboard={!loading}
        onCancel={() => {
          if (loading) return;
          setConfirming(false);
          setOpen(true);
        }}
        onOk={() => void run(reasonSnapshot)}
        okText={t("common.confirmExecute")}
        okButtonProps={{ danger, loading }}
      >
        <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
          {targetLine}
          <span>{confirmText}</span>
          {reasonSnapshot && (
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("common.reasonLabel")}:{reasonSnapshot}
            </Typography.Text>
          )}
        </Space>
      </Modal>
    </>
  );
}
