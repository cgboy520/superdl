/** 高危操作统一模式:原因必填 → 二次确认 → 执行 → message 反馈;无权角色按钮可见但禁用 + tooltip。 */

import { App, Button, Form, Input, Modal, Tooltip } from "antd";
import type { ButtonProps } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiErrorText } from "@superdl/ui";

import { REASON_MAX_LEN } from "../lib/validators";

interface Props {
  label: string;
  title: string;
  confirmText: string;
  danger?: boolean;
  disabled?: boolean;
  /** 禁用原因(tooltip) */
  disabledReason: string;
  /** 触发按钮尺寸(默认 small) */
  size?: ButtonProps["size"];
  /** 返回字符串作成功提示,否则用通用文案 */
  onSubmit: (reason: string) => Promise<string | void>;
}

export function ReasonAction({
  label,
  title,
  confirmText,
  danger,
  disabled,
  disabledReason,
  size = "small",
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  // 原因快照:第二步提交从快照取
  const [reasonSnapshot, setReasonSnapshot] = useState("");
  const [form] = Form.useForm<{ reason: string }>();

  const button = (
    <Button danger={danger} size={size} disabled={disabled} onClick={() => setOpen(true)}>
      {label}
    </Button>
  );

  const run = async () => {
    setLoading(true);
    try {
      const custom = await onSubmit(reasonSnapshot);
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
          // 先关原因弹窗再开二次确认
          setReasonSnapshot(form.getFieldValue("reason") as string);
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
            <Input.TextArea rows={3} maxLength={REASON_MAX_LEN} showCount placeholder={t("common.reasonPlaceholder")} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={t("common.secondConfirm")}
        open={confirming}
        // 提交在途禁止关闭
        mask={{ closable: !loading }}
        keyboard={!loading}
        onCancel={() => {
          if (loading) return;
          // 取消返回第一步(原因保留)
          setConfirming(false);
          setOpen(true);
        }}
        onOk={run}
        okText={t("common.confirmExecute")}
        okButtonProps={{ danger, loading }}
      >
        {confirmText}
      </Modal>
    </>
  );
}
