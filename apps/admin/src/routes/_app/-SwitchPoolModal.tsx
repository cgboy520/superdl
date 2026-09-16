/** Pool switch modal: target pool and reason, submitted after a second confirmation. */

import { Alert, App, Button, Form, Input, Modal, Select, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fontSize, space, useApiErrorText } from "@superdl/ui";
import { useConfirm } from "@superdl/ui/components";

import { type NodeRow, useSwitchNodePool } from "../../api";
import { POOL_LABEL_KEY, SWITCHABLE_POOLS, type SwitchablePool } from "../../lib/pools";
import { REASON_MAX_LEN } from "../../lib/validators";

interface FormValues {
  pool: SwitchablePool;
  reason: string;
}

/** The current pool prefers the desired pool (pool_label still holds the old value mid-switch). */
export function currentPool(node: NodeRow): string {
  return node.desired_pool || node.pool_label || "";
}

/** Whether the node takes part in pool switching: labelled nodes with cards; when the observed card count drops to 0 because the target pool's component is down, being in a GPU pool still counts. */
export function inSwitchablePool(node: NodeRow): boolean {
  const from = currentPool(node);
  if (!from || from === "cpu") return false;
  return node.gpu_total > 0 || (SWITCHABLE_POOLS as readonly string[]).includes(from);
}

/** Switchable: takes part in switching and the model still has at least one selectable target pool. */
export function canSwitchPool(node: NodeRow): boolean {
  return inSwitchablePool(node) && switchTargets(node).some((target) => !target.disabled);
}

/** Exclude the current pool; models the backend marks as not MIG-capable disable mig, not passthrough-capable disable kata. */
export function switchTargets(node: NodeRow): { pool: SwitchablePool; disabled: boolean }[] {
  const blockedPools = new Set<SwitchablePool>();
  if (!node.supports_mig) blockedPools.add("mig");
  if (!node.supports_passthrough) blockedPools.add("kata");
  const from = currentPool(node);
  return SWITCHABLE_POOLS.filter((p) => p !== from).map((p) => ({
    pool: p,
    disabled: blockedPools.has(p),
  }));
}

export function SwitchPoolModal({
  node,
  onClose,
  onDone,
}: {
  /** undefined = closed */
  node: NodeRow | undefined;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const confirm = useConfirm();
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const target = Form.useWatch("pool", form);
  const switchPool = useSwitchNodePool({
    mutation: {
      onSuccess: (r) => {
        message.success(t("nodes.switchPoolSubmitted", { to: r.to_pool }));
        onDone();
        close();
      },
      onError: (e) => message.error(errText(e, t("nodes.switchPoolFailed"))),
    },
  });

  const close = () => {
    form.resetFields();
    onClose();
  };

  if (!node) return null;
  const from = currentPool(node);
  // an unrecognised model is the placeholder "GPU" in the API (router_nodes: gpu_model or "GPU"); fall back to the raw probe string
  const model =
    node.gpu_model && node.gpu_model !== "GPU" ? node.gpu_model : node.gpu_model_raw || t("nodes.unrecognizedTag");
  const blockedReason: Partial<Record<SwitchablePool, string>> = {
    kata: t("nodes.switchPoolPassthroughUnsupported", { model }),
    mig: t("nodes.switchPoolMigUnsupported", { model }),
  };
  const options = switchTargets(node).map(({ pool, disabled }) => ({
    value: pool,
    label: t(POOL_LABEL_KEY[pool]),
    disabled,
    title: disabled ? blockedReason[pool] : undefined,
  }));

  const submit = () => {
    void (async () => {
      let values: FormValues;
      try {
        values = await form.validateFields();
      } catch {
        return;
      }
      confirm({
        title: t("nodes.switchPoolConfirmTitle", { name: node.name, from, to: values.pool }),
        consequences: [
          t("nodes.switchPoolConsequenceCordon"),
          t("nodes.switchPoolConsequenceLabels"),
          t("nodes.switchPoolConsequenceOperands"),
          t("nodes.switchPoolConsequenceCapacity", { from, to: values.pool }),
        ],
        impact: t("nodes.switchPoolReasonEcho", { reason: values.reason }),
        okText: t("common.confirmExecute"),
        danger: true,
        onOk: async () => {
          await switchPool.mutateAsync({
            nodeName: node.name,
            data: { pool: values.pool, reason: values.reason },
          });
        },
      });
    })();
  };

  return (
    <Modal
      title={t("nodes.switchPoolTitle")}
      open
      onCancel={close}
      width="min(640px, 100vw)"
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={close}>{t("common.cancel", { ns: "shared" })}</Button>
          <Button type="primary" danger loading={switchPool.isPending} onClick={submit}>
            {t("common.next")}
          </Button>
        </Space>
      }
    >
      <Typography.Text
        type="secondary"
        style={{ display: "block", fontSize: fontSize.caption, marginBottom: space.sm }}
      >
        {t("common.targetLabel")}:{node.name}
      </Typography.Text>
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Alert
          type="warning"
          showIcon
          title={target === "kata" ? t("nodes.switchPoolNoticeKata") : t("nodes.switchPoolNotice")}
        />
        <Form form={form} layout="vertical" initialValues={{ pool: options.find((o) => !o.disabled)?.value }}>
          <Form.Item label={t("nodes.switchPoolFrom")}>
            <Typography.Text>{from ? t(POOL_LABEL_KEY[from as SwitchablePool]) : "—"}</Typography.Text>
          </Form.Item>
          <Form.Item
            name="pool"
            label={t("nodes.switchPoolTo")}
            rules={[{ required: true, message: t("nodes.switchPoolToRule") }]}
          >
            <Select options={options} />
          </Form.Item>
          <Form.Item
            name="reason"
            label={t("common.reasonLabel")}
            rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
          >
            <Input.TextArea rows={3} maxLength={REASON_MAX_LEN} showCount placeholder={t("common.reasonPlaceholder")} />
          </Form.Item>
        </Form>
      </Space>
    </Modal>
  );
}
