/** 切换节点池:表单态(当前池 / 目标池 / 原因)→ L2 二次确认 → 回执态(重跑命令,令牌只显示一次)。
 *  前置由节点行的 GatedButton 挡住,这里只做取值闸(排除当前池、机型不支持 MIG 的灰置)。 */

import { Alert, App, Button, Form, Input, Modal, Select, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { fontSize, space, useApiErrorText } from "@superdl/ui";
import { useConfirm } from "@superdl/ui/components";

import { type NodeRow, type NodeSwitchPoolOut, useSwitchNodePool } from "../../api";
import { REASON_MAX_LEN } from "../../lib/validators";
import { POOL_LABEL_KEY, SWITCHABLE_POOLS, supportsMig, type SwitchablePool } from "../../lib/pools";
import { CommandPanel } from "./-AddNodeModal";

interface FormValues {
  pool: SwitchablePool;
  reason: string;
}

/** 当前池取期望池优先(切换中时 pool_label 还是旧值)。 */
export function currentPool(node: NodeRow): string {
  return node.desired_pool || node.pool_label || "";
}

/** 目标池候选:排除当前池,机型不支持 MIG 时 mig 灰置而非隐藏(条件操作可见但禁用)。
 *  与后端 switch_node_pool 的取值闸同口径,纯函数以便单测。 */
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
  const [result, setResult] = useState<NodeSwitchPoolOut | null>(null);
  const [idemKey, setIdemKey] = useState(() => crypto.randomUUID());
  const switchPool = useSwitchNodePool({
    mutation: {
      onSuccess: (r) => {
        setResult(r);
        onDone();
      },
      onError: (e) => message.error(errText(e, t("nodes.switchPoolFailed"))),
    },
  });

  const close = () => {
    setResult(null);
    form.resetFields();
    setIdemKey(crypto.randomUUID());
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
        return; // antd 已给红字
      }
      confirm({
        title: t("nodes.switchPoolConfirmTitle", { name: node.name, from, to: values.pool }),
        consequences: [
          t("nodes.switchPoolConsequenceCordon"),
          t("nodes.switchPoolConsequenceLabels"),
          t("nodes.switchPoolConsequenceRerun"),
          t("nodes.switchPoolConsequenceCapacity", { from, to: values.pool }),
        ],
        impact: t("nodes.switchPoolReasonEcho", { reason: values.reason }),
        okText: t("common.confirmExecute"),
        danger: true,
        onOk: async () => {
          await switchPool.mutateAsync({
            nodeName: node.name,
            data: { pool: values.pool, reason: values.reason },
            idempotencyKey: idemKey,
          });
        },
      });
    })();
  };

  return (
    <Modal
      title={result ? t("nodes.switchPoolDoneTitle") : t("nodes.switchPoolTitle")}
      open
      onCancel={close}
      width="min(640px, 100vw)"
      destroyOnHidden
      footer={
        result ? (
          <Button type="primary" onClick={close}>
            {t("nodes.done")}
          </Button>
        ) : (
          <Space>
            <Button onClick={close}>{t("common.cancel", { ns: "shared" })}</Button>
            <Button type="primary" danger loading={switchPool.isPending} onClick={submit}>
              {t("common.next")}
            </Button>
          </Space>
        )
      }
    >
      <Typography.Text
        type="secondary"
        style={{ display: "block", fontSize: fontSize.caption, marginBottom: space.sm }}
      >
        {t("common.targetLabel")}:{node.name}
      </Typography.Text>
      {result ? (
        <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            title={t("nodes.switchPoolDoneTitle")}
            description={t("nodes.switchPoolDoneDesc", { to: result.to_pool })}
          />
          <CommandPanel result={result} />
        </Space>
      ) : (
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
              <Input.TextArea
                rows={3}
                maxLength={REASON_MAX_LEN}
                showCount
                placeholder={t("common.reasonPlaceholder")}
              />
            </Form.Item>
          </Form>
        </Space>
      )}
    </Modal>
  );
}
