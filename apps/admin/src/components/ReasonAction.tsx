/** 高危操作统一模式:原因必填 → 二次确认 → 执行 → message 反馈;无权角色按钮可见但禁用 + tooltip。
 *  target(目标标识:租户 #id·手机 / 实例名·uuid 前缀 / 节点名 / 退款单号)在两步弹窗都显示,操作者看得见自己在动哪一条。 */

import { fontSize, space } from "@superdl/ui";
import { App, Button, Form, Input, Modal, Space, Tooltip, Typography } from "antd";
import type { ButtonProps } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useApiErrorText } from "@superdl/ui";

import { REASON_MAX_LEN } from "../lib/validators";

interface Props {
  label: string;
  title: string;
  confirmText: string;
  /** 目标标识(两步弹窗顶部回显) */
  target?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  /** 禁用原因(tooltip) */
  disabledReason: string;
  /** 触发按钮尺寸(默认 small) */
  size?: ButtonProps["size"];
  /** 触发按钮形态(行内「更多」里用 link) */
  type?: ButtonProps["type"];
  /** 返回字符串作成功提示,否则用通用文案 */
  onSubmit: (reason: string) => Promise<string | void>;
}

export function ReasonAction({
  label,
  title,
  confirmText,
  target,
  danger,
  disabled,
  disabledReason,
  size = "small",
  type,
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

  // 禁用态保持可聚焦(aria-disabled + 拦截点击),键盘用户也能读到 tooltip 里的原因
  const button = (
    <Button
      danger={danger}
      size={size}
      type={type}
      aria-disabled={disabled || undefined}
      className={disabled ? "btn-aria-disabled" : undefined}
      onClick={() => {
        if (!disabled) setOpen(true);
      }}
    >
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
    // 先关原因弹窗再开二次确认
    setReasonSnapshot(form.getFieldValue("reason") as string);
    setOpen(false);
    setConfirming(true);
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
        onOk={goNext}
        okText={t("common.next")}
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
              // Ctrl/⌘+Enter 进下一步
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
