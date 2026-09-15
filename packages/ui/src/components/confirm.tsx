/** 确认强度组件(ui-ux-spec §1 规则 7):useConfirm(L2)= 后果前置 + 影响说明的 modal.confirm;
 *  TypeConfirmModal(L3)= 键入名称 + 可选勾选双闸,用于终态动作。 */

import { App, Button, Checkbox, Input, Modal, Space, Typography } from "antd";
import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { space } from "../tokens";

export interface ConfirmOptions {
  title: ReactNode;
  /** 后果条目(如「GPU 立即释放,再开机可能库存不足」) */
  consequences: ReactNode[];
  /** 影响说明(如「该操作影响 3 台在跑实例」) */
  impact?: ReactNode;
  okText?: string;
  cancelText?: string;
  /** 危险动作(红色按钮) */
  danger?: boolean;
  /** 确认按钮禁用(如影响面查询在途) */
  okDisabled?: boolean;
  onOk: () => void | Promise<void>;
}

/** L2 确认:后果前置 + 影响说明。须在 <AntApp> 内使用。 */
export function useConfirm() {
  const { modal } = App.useApp();
  return useCallback(
    (opts: ConfirmOptions) => {
      modal.confirm({
        title: opts.title,
        content: (
          <Space orientation="vertical" size={space.xs} style={{ width: "100%" }}>
            {opts.consequences.map((line, i) => (
              <Typography.Paragraph key={i} style={{ marginBottom: 0 }}>
                {line}
              </Typography.Paragraph>
            ))}
            {opts.impact ? <Typography.Text type="secondary">{opts.impact}</Typography.Text> : null}
          </Space>
        ),
        okText: opts.okText,
        cancelText: opts.cancelText,
        okButtonProps: {
          ...(opts.danger ? { danger: true } : {}),
          ...(opts.okDisabled ? { disabled: true } : {}),
        },
        onOk: opts.onOk,
      });
    },
    [modal],
  );
}

export interface TypeConfirmModalProps {
  open: boolean;
  title: ReactNode;
  /** 后果说明段落(必填) */
  body: ReactNode;
  /** 需键入以解锁的目标名(实例名/盘名/手机号) */
  targetName: string;
  /** 第二道闸勾选文案;不传则只有键入一道闸 */
  checkboxLabel?: ReactNode;
  confirmLabel: ReactNode;
  cancelLabel: ReactNode;
  loading?: boolean;
  /** 键入框 maxLength,默认 64 */
  maxLength?: number;
  /** 附加解锁条件:true 时确认保持禁用 */
  extraDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** L3 破坏确认:键入目标名(+ 勾选)全过才解锁红色按钮;关闭即清空。 */
export function TypeConfirmModal({
  open,
  title,
  body,
  targetName,
  checkboxLabel,
  confirmLabel,
  cancelLabel,
  loading,
  maxLength = 64,
  extraDisabled = false,
  onConfirm,
  onCancel,
}: TypeConfirmModalProps) {
  const { t } = useTranslation("shared");
  const [typed, setTyped] = useState("");
  const [acked, setAcked] = useState(false);
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (!open) {
      setTyped("");
      setAcked(false);
    }
  }
  const unlocked = typed.trim() === targetName && (checkboxLabel == null || acked) && !extraDisabled;
  const close = () => {
    setTyped("");
    setAcked(false);
    onCancel();
  };
  return (
    <Modal
      title={title}
      open={open}
      onCancel={close}
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={close}>{cancelLabel}</Button>
          <Button danger type="primary" disabled={!unlocked} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph>{body}</Typography.Paragraph>
      <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("confirm.typeNameToConfirm", { name: targetName })}</Typography.Text>
        <Input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={targetName}
          maxLength={maxLength}
          autoComplete="off"
          aria-label={t("confirm.typeNameToConfirm", { name: targetName })}
        />
        {checkboxLabel != null && (
          <Checkbox checked={acked} onChange={(e) => setAcked(e.target.checked)}>
            {checkboxLabel}
          </Checkbox>
        )}
      </Space>
    </Modal>
  );
}
