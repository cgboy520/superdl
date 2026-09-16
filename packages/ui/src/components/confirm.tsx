/** Confirmation strength components (ui-ux-spec §1 rule 8): useConfirm (L2) = modal.confirm with consequences first + impact;
 *  TypeConfirmModal (L3) = type the name + optional checkbox, two gates, for terminal actions. */

import { App, Button, Checkbox, Input, Modal, Space, Typography } from "antd";
import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { space } from "../tokens";

export interface ConfirmOptions {
  title: ReactNode;
  /** Consequence items (e.g. "The GPU is released at once, stock may be short at the next start") */
  consequences: ReactNode[];
  /** Impact note (e.g. "This affects 3 running instances") */
  impact?: ReactNode;
  okText?: string;
  cancelText?: string;
  /** Dangerous action (red button) */
  danger?: boolean;
  /** Confirm button disabled (e.g. impact query in flight) */
  okDisabled?: boolean;
  onOk: () => void | Promise<void>;
}

/** L2 confirmation: consequences first + impact. Must be used inside <AntApp>. */
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
  /** Consequence paragraph (required) */
  body: ReactNode;
  /** Target name to type for unlocking (instance name / disk name / handle) */
  targetName: string;
  /** Second-gate checkbox copy; omitted = the typing gate only */
  checkboxLabel?: ReactNode;
  confirmLabel: ReactNode;
  cancelLabel: ReactNode;
  loading?: boolean;
  /** maxLength of the input, default 64 */
  maxLength?: number;
  /** Extra unlock condition: true keeps confirm disabled */
  extraDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** L3 destructive confirmation: the red button unlocks only once the target name is typed (+ the checkbox ticked); closing clears everything. */
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
