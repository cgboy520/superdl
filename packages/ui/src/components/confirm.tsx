/** 确认强度组件(ui-ux-spec §1 规则 7 的共享实现):
 *  - useConfirm(L2):modal.confirm 的统一形态,「后果前置 + 影响说明」由结构强制,
 *    替代各页手写 confirm 时自由发挥的句式。
 *  - TypeConfirmModal(L3):「键入名称 + 可选勾选」双闸确认,收编释放实例/删除数据盘/
 *    注销账号三处自实现;勾选闸可省(creating 态取消创建这类「尚未落盘」场景)。 */

import { App, Button, Checkbox, Input, Modal, Space, Typography } from "antd";
import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface ConfirmOptions {
  title: ReactNode;
  /** 后果前置:逐条列出本动作的后果(句式由调用方写全,如「GPU 立即释放,再开机可能库存不足」) */
  consequences: ReactNode[];
  /** 影响说明(可选,如「该操作影响 3 台在跑实例」),显示在后果列表之后 */
  impact?: ReactNode;
  okText?: string;
  cancelText?: string;
  /** 危险动作(红色按钮);默认可逆动作为主色 */
  danger?: boolean;
  onOk: () => void | Promise<void>;
}

/** L2 确认:后果前置 + 影响说明的统一句式。必须在 <AntApp> 上下文内使用。 */
export function useConfirm() {
  const { modal } = App.useApp();
  return useCallback(
    (opts: ConfirmOptions) => {
      modal.confirm({
        title: opts.title,
        content: (
          <Space orientation="vertical" size={4} style={{ width: "100%" }}>
            {opts.consequences.map((line, i) => (
              <Typography.Paragraph key={i} style={{ marginBottom: 0 }}>
                {line}
              </Typography.Paragraph>
            ))}
            {opts.impact ? (
              <Typography.Text type="secondary">{opts.impact}</Typography.Text>
            ) : null}
          </Space>
        ),
        okText: opts.okText,
        cancelText: opts.cancelText,
        okButtonProps: opts.danger ? { danger: true } : undefined,
        onOk: opts.onOk,
      });
    },
    [modal],
  );
}

export interface TypeConfirmModalProps {
  open: boolean;
  title: ReactNode;
  /** 后果说明段落(后果前置,必填:终态动作必须把代价写在确认按钮前) */
  body: ReactNode;
  /** 需键入以解锁的目标名(实例名/盘名/手机号) */
  targetName: string;
  /** 第二道闸文案(如「我确认将清除实例盘全部数据」);不传则只有键入一道闸 */
  checkboxLabel?: ReactNode;
  confirmLabel: ReactNode;
  cancelLabel: ReactNode;
  loading?: boolean;
  /** 键入框 maxLength,默认 64 */
  maxLength?: number;
  /** 附加解锁条件(body 里内联的额外必填项,如注销的原因字段):true 时确认保持禁用 */
  extraDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** L3 破坏确认:键入目标名(+ 勾选知情)两道闸全过才解锁红色按钮。
 *  关闭时自动清空键入与勾选,重开是全新一轮。 */
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
  // 任何路径关闭(取消/成功/遮罩/Esc)后重开都是全新一轮:渲染期派生重置
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
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          {t("confirm.typeNameToConfirm", { name: targetName })}
        </Typography.Text>
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
