/**
 * 风险商品知情同意 modal(ui-ux-spec 规则 5):逐条列明 + 必勾复选框,勾选前主按钮禁用。
 *
 * 经济档(软切分超卖)与竞价档(可被回收)共用这一套形态 —— 两处各写一遍,
 * 迟早出现「一处能不勾就继续、另一处不能」这类没人会去对的差异。
 * 条目文案由调用方给(它们是两件不同的事),交互与解锁判据在这里只有一份。
 */

import { Button, Checkbox, Modal, Space } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export function ConsentModal({
  open,
  title,
  lines,
  agreeLabel,
  confirmLabel,
  loading,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  /** 逐条列明的知情事项 */
  lines: string[];
  /** 复选框文案(「我已知悉…」) */
  agreeLabel: string;
  /** 主按钮文案 */
  confirmLabel: string;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const [checked, setChecked] = useState(false);
  const close = () => {
    setChecked(false);
    onCancel();
  };
  return (
    <Modal
      title={title}
      open={open}
      onCancel={close}
      footer={
        <Space>
          <Button onClick={close}>{t("create.cancel")}</Button>
          <Button
            type="primary"
            disabled={!checked}
            loading={loading}
            onClick={() => {
              setChecked(false);
              onConfirm();
            }}
          >
            {confirmLabel}
          </Button>
        </Space>
      }
    >
      <ul style={{ paddingLeft: 20 }}>
        {lines.map((line) => (
          <li key={line} style={{ marginBottom: 8 }}>
            {line}
          </li>
        ))}
      </ul>
      <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>
        {agreeLabel}
      </Checkbox>
    </Modal>
  );
}
