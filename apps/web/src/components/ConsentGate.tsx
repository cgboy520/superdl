/** 知情同意闸(ui-ux-spec §1 规则 6):竞价 / 共享·经济各一节,命中几节出几节,一个 modal 一次勾选;
 *  创建失败重试不重置勾选(只在取消 / 关闭时重置)。竞价条目 ②③④ 对应 orchestrator/preempt.py 的硬规矩,折扣与宽限秒数从 /policies 读。 */

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
  /** 「我已知悉,继续创建 / 继续部署」由调用方给 */
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

/** 把「提交 → 命中的知情同意 → 真正提交」串起来:命中零节直接提交,否则弹一个分节 modal。 */
export function useConsentGate({
  spot,
  eco,
  spotPolicy,
  confirmLabel,
  loading,
  onProceed,
}: {
  /** 竞价档命中 */
  spot: boolean;
  /** 共享·经济(hami 池)命中 */
  eco: boolean;
  spotPolicy: SpotPolicy | undefined;
  confirmLabel: string;
  loading?: boolean;
  /** 返回 Promise 时等它落定再收窗(确认按钮上的 loading 才看得见);同步实现则立刻收窗 */
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
        // 先提交后关窗:提交在途时确认按钮亮 loading,落定(成功导航走 / 失败页面出错误提示)才收窗。
        // 失败再点提交会重新弹,勾选态留在 modal 内。
        const proceeding = onProceed();
        if (proceeding instanceof Promise) void proceeding.finally(() => setOpen(false));
        else setOpen(false);
      }}
    />
  );
  return { submit, modal };
}
