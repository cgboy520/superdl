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
  /** 禁用原因(tooltip)。无权限/状态不符时必填 */
  disabledReason: string;
  /** 触发按钮尺寸(表格内默认 small;卡片 extra 等场景传 middle) */
  size?: ButtonProps["size"];
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
  size = "small",
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  // 原因快照:第一步弹窗 destroyOnHidden 销毁后 getFieldsValue 只回已挂载字段,第二步提交须从快照取
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
        // 提交飞行中禁止关闭:蒙层/ESC 关弹窗而请求仍在途,用户会误以为没提交而重试
        mask={{ closable: !loading }}
        keyboard={!loading}
        onCancel={() => {
          if (loading) return;
          // 取消返回第一步重开原因弹窗(原因留在 form store,不丢),而非终结整个流程
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
