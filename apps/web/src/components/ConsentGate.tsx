import { fontSize, space } from "@superdl/ui";
import { Button, Checkbox, Modal, Space, Typography } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { SpotPolicy } from "./spotBilling";

export interface ConsentSection {
  key: "spot" | "eco";
  title: string;
  lines: string[];
}

/** 分节知情同意弹窗;取消按钮或关闭操作重置勾选,仅切换 open 不重置。 */
export function ConsentGate({
  open,
  sections,
  confirmLabel,
  loading,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  sections: ConsentSection[];
  confirmLabel: string;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const [checked, setChecked] = useState(false);
  const close = () => {
    setChecked(false);
    onCancel();
  };
  const first = sections[0];
  const title = sections.length === 1 && first ? first.title : t("consent.titleBoth");
  return (
    <Modal
      title={title}
      open={open}
      onCancel={close}
      footer={
        <Space>
          <Button onClick={close}>{t("common.cancel", { ns: "shared" })}</Button>
          <Button type="primary" disabled={!checked} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </Space>
      }
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        {sections.map((s) => (
          <div key={s.key}>
            {sections.length > 1 && (
              <Typography.Text strong style={{ display: "block", marginBottom: space.xs }}>
                {s.title}
              </Typography.Text>
            )}
            <ul style={{ paddingInlineStart: 20, margin: 0 }}>
              {s.lines.map((line) => (
                <li key={line} style={{ marginBottom: space.xs, fontSize: fontSize.body }}>
                  {line}
                </li>
              ))}
            </ul>
          </div>
        ))}
        <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>
          {t("consent.agreeAll")}
        </Checkbox>
      </Space>
    </Modal>
  );
}

/** 无同意条目时直接提交,否则显示分节确认弹窗。 */
export function useConsentGate({
  spot,
  eco,
  spotPolicy,
  confirmLabel,
  loading,
  onProceed,
}: {
  spot: boolean;
  /** 共享·经济档位。 */
  eco: boolean;
  spotPolicy: SpotPolicy | undefined;
  confirmLabel: string;
  loading?: boolean;
  /** 返回 Promise 时等待其结束再关闭弹窗,否则立即关闭。 */
  onProceed: () => void | Promise<void>;
}): { submit: () => void; modal: ReactNode } {
  const { t } = useTranslation(["web", "shared"]);
  const [open, setOpen] = useState(false);
  const sections: ConsentSection[] = [];
  if (spot && spotPolicy) {
    sections.push({
      key: "spot",
      title: t("create.spotModalTitle"),
      lines: [
        t("copy.spotConsent.c1", { pct: spotPolicy.discountPct }),
        t("copy.spotConsent.c2"),
        t("copy.spotConsent.c3", { seconds: spotPolicy.graceSeconds }),
        t("copy.spotConsent.c4"),
        t("copy.spotConsent.c5"),
      ],
    });
  }
  if (eco) {
    sections.push({
      key: "eco",
      title: t("create.ecoModalTitle"),
      lines: [
        t("copy.ecoTierConsent.c1"),
        t("copy.ecoTierConsent.c2"),
        t("copy.ecoTierConsent.c3"),
        t("copy.ecoTierConsent.c4"),
      ],
    });
  }
  const submit = () => {
    if (sections.length === 0) void onProceed();
    else setOpen(true);
  };
  const modal = (
    <ConsentGate
      open={open}
      sections={sections}
      confirmLabel={confirmLabel}
      loading={loading}
      onCancel={() => setOpen(false)}
      onConfirm={() => {
        const proceeding = onProceed();
        if (proceeding instanceof Promise) void proceeding.finally(() => setOpen(false));
        else setOpen(false);
      }}
    />
  );
  return { submit, modal };
}
