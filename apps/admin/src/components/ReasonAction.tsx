/**
 * 高危操作统一模式:原因必填 → 二次确认 → 执行 → message 反馈。
 * readonly 等无权角色:按钮可见但禁用 + tooltip 说明(条件操作可见原则)。
 */

import { App, Button, Form, Input, Modal, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiErrorText } from "../lib/apiError";

interface Props {
  label: string;
  title: string;
  /** 二次确认文案 */
  confirmText: string;
  danger?: boolean;
  size?: "small" | "middle";
  disabled?: boolean;
  /** 禁用原因(tooltip)。无权限/状态不符时必填 */
  disabledReason?: string;
  onSubmit: (reason: string) => Promise<void>;
}

export function ReasonAction({
  label,
  title,
  confirmText,
  danger,
  size = "small",
  disabled,
  disabledReason,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ reason: string }>();

  const button = (
    <Button danger={danger} size={size} disabled={disabled} onClick={() => setOpen(true)}>
      {label}
    </Button>
  );

  const run = async () => {
    const { reason } = form.getFieldsValue();
    setLoading(true);
    try {
      await onSubmit(reason);
      message.success(t("common.actionDone", { action: title }));
      setOpen(false);
      setConfirming(false);
      form.resetFields();
    } catch (e) {
      message.error(errText(e, t("common.actionFailed", { action: title })));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {disabled && disabledReason ? <Tooltip title={disabledReason}>{button}</Tooltip> : button}
      <Modal
        title={title}
        open={open}
        onCancel={() => {
          setOpen(false);
          setConfirming(false);
        }}
        onOk={async () => {
          try {
            await form.validateFields();
          } catch {
            return;
          }
          setConfirming(true);
        }}
        okText={t("common.next")}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="reason"
            label={t("common.reasonLabel")}
            rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
          >
            <Input.TextArea rows={3} placeholder={t("common.reasonPlaceholder")} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={t("common.secondConfirm")}
        open={confirming}
        onCancel={() => setConfirming(false)}
        onOk={run}
        okText={t("common.confirmExecute")}
        okButtonProps={{ danger, loading }}
      >
        {confirmText}
      </Modal>
    </>
  );
}
