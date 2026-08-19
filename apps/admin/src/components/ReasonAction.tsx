/**
 * 高危操作统一模式:原因必填 → 二次确认 → 执行 → message 反馈。
 * readonly 等无权角色:按钮可见但禁用 + tooltip 说明(条件操作可见原则)。
 */

import { App, Button, Form, Input, Modal, Tooltip } from "antd";
import { useState } from "react";

import { isApiError } from "../api";

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
      message.success(`${title}已执行`);
      setOpen(false);
      setConfirming(false);
      form.resetFields();
    } catch (e) {
      message.error(isApiError(e) ? e.message : `${title}失败`);
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
        okText="下一步"
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="reason"
            label="操作原因(必填,入审计)"
            rules={[{ required: true, min: 2, message: "请填写至少 2 个字的原因" }]}
          >
            <Input.TextArea rows={3} placeholder="原因将写入审计日志" />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title="二次确认"
        open={confirming}
        onCancel={() => setConfirming(false)}
        onOk={run}
        okText="确认执行"
        okButtonProps={{ danger, loading }}
      >
        {confirmText}
      </Modal>
    </>
  );
}
