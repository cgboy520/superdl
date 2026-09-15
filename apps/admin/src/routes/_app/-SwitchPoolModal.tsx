/** 切池弹窗:填写目标池与原因,二次确认后提交。 */

import { Alert, App, Button, Form, Input, Modal, Select, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { fontSize, space, useApiErrorText } from "@superdl/ui";
import { useConfirm } from "@superdl/ui/components";

import { type NodeRow, useSwitchNodePool } from "../../api";
import { POOL_LABEL_KEY, SWITCHABLE_POOLS, supportsMig, type SwitchablePool } from "../../lib/pools";
import { REASON_MAX_LEN } from "../../lib/validators";

interface FormValues {
  pool: SwitchablePool;
  reason: string;
}

/** 当前池取期望池优先(切换中时 pool_label 还是旧值)。 */
export function currentPool(node: NodeRow): string {
  return node.desired_pool || node.pool_label || "";
}

/** 排除当前池;机型不支持 MIG 时将 mig 标为禁用。 */
export function switchTargets(node: NodeRow): { pool: SwitchablePool; disabled: boolean }[] {
  const from = currentPool(node);
  const migOk = supportsMig(node.gpu_model);
  return SWITCHABLE_POOLS.filter((p) => p !== from).map((p) => ({
    pool: p,
    disabled: p === "mig" && !migOk,
  }));
}

export function SwitchPoolModal({
  node,
  onClose,
  onDone,
}: {
  /** undefined = 关闭 */
  node: NodeRow | undefined;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const confirm = useConfirm();
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
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
  const options = switchTargets(node).map(({ pool, disabled }) => ({
    value: pool,
    label: t(POOL_LABEL_KEY[pool]),
    disabled,
    title: disabled ? t("nodes.switchPoolMigUnsupported", { model: node.gpu_model }) : undefined,
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
        <Alert type="warning" showIcon title={t("nodes.switchPoolNotice")} />
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
