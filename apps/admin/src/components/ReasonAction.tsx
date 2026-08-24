/**
 * 高危操作统一模式:原因必填 → 二次确认 → 执行 → message 反馈。
 * readonly 等无权角色:按钮可见但禁用 + tooltip 说明。
 */

import { App, Button, Form, Input, Modal, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiErrorText } from "../lib/apiError";

interface Props {
  label: string;
  title: string;
  confirmText: string;
  danger?: boolean;
  disabled?: boolean;
  /** 禁用原因(tooltip)。无权限/状态不符时必填 */
  disabledReason?: string;
  /** 返回字符串则作为成功提示(用于回显影响面,如「已停 N 台」),否则用通用文案 */
  onSubmit: (reason: string) => Promise<string | void>;
}

export function ReasonAction({
  label,
  title,
  confirmText,
  danger,
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
    <Button danger={danger} size="small" disabled={disabled} onClick={() => setOpen(true)}>
      {label}
    </Button>
  );

  const run = async () => {
    const { reason } = form.getFieldsValue();
    setLoading(true);
    try {
      const custom = await onSubmit(reason);
      message.success(typeof custom === "string" ? custom : t("common.actionDone", { action: title }));
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
          // 先关原因弹窗再开二次确认:两层 Modal 叠开时 ESC/蒙层会误关底下那层
          setOpen(false);
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
