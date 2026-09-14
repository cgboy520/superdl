/** 添加节点:生成注册命令(池 / 主机名 / 备注)+ 命令展示与复制。 */

import { Alert, App, Button, Form, Input, InputNumber, Modal, Select, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { fontSize, formatDateTime, space } from "@superdl/ui";
import { CopyField } from "@superdl/ui/components";
import { useApiErrorText } from "@superdl/ui";
import { useFormDraft } from "@superdl/ui";

import { type EnrollmentCommandOut, useCreateEnrollment } from "../../api";
import { POOL_LABEL_KEY, type Pool } from "../../lib/pools";

/** 命令展示(创建/重新生成共用):令牌只显示这一次 */
export function CommandPanel({ result }: { result: EnrollmentCommandOut }) {
  const { t } = useTranslation();
  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <Alert
        type="warning"
        showIcon
        title={t("nodes.tokenOnce")}
        description={t("nodes.tokenOnceDesc", { time: formatDateTime(result.enrollment.expires_at) })}
      />
      <div>
        <Typography.Text type="secondary">{t("nodes.cmdPiped")}</Typography.Text>
        <div style={{ margin: "4px 0 8px" }}>
          <CopyField value={result.curl_command} code block />
        </div>
        <Typography.Text type="secondary">{t("nodes.cmdCautious")}</Typography.Text>
        <div style={{ marginTop: 4 }}>
          <CopyField value={result.wget_command} code block />
        </div>
      </div>
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        {t("nodes.cmdFootnote")}
      </Typography.Text>
    </Space>
  );
}

export interface EnrollFormValues {
  pool: Pool;
  hostname: string;
  note?: string;
  nvme_devices?: string[];
  ttl_hours: number;
}

// 与后端 nodes/schemas.py HOSTNAME_PATTERN 对齐
export const HOSTNAME_PATTERN = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;

export function AddNodeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [form] = Form.useForm<EnrollFormValues>();
  // 新建草稿(sessionStorage),生成命令成功后清除
  const draft = useFormDraft<EnrollFormValues>("node-new");
  const [result, setResult] = useState<EnrollmentCommandOut | null>(null);
  const [idemKey, setIdemKey] = useState(() => crypto.randomUUID());
  const create = useCreateEnrollment({
    mutation: {
      onSuccess: (r) => {
        draft.clear();
        setResult(r);
      },
      onError: (e) => message.error(errText(e, t("nodes.generateFailed"))),
    },
  });

  const close = () => {
    setResult(null);
    form.resetFields();
    // 幂等键随下一次注册轮换
    setIdemKey(crypto.randomUUID());
    onClose();
  };

  return (
    <Modal
      title={result ? t("nodes.cmdModalTitle") : t("nodes.addNode")}
      open={open}
      onCancel={close}
      footer={
        result ? (
          <Button type="primary" onClick={close}>
            {t("nodes.done")}
          </Button>
        ) : (
          <Button
            type="primary"
            loading={create.isPending}
            onClick={() => {
              void (async () => {
                try {
                  const values = await form.validateFields();
                  create.mutate({ data: values, idempotencyKey: idemKey });
                } catch {
                  // 校验失败:antd 已给红字
                }
              })();
            }}
          >
            {t("nodes.generateCmd")}
          </Button>
        )
      }
      width="min(640px, 100vw)"
      destroyOnHidden
    >
      {result ? (
        <CommandPanel result={result} />
      ) : (
        <Form form={form} layout="vertical" initialValues={{ pool: "hami", ttl_hours: 24 }}>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            title={t("nodes.poolRule")}
            description={t("nodes.poolRuleDesc")}
          />
          <Form.Item name="pool" label={t("nodes.poolLabel")} rules={[{ required: true }]}>
            <Select
              options={Object.entries(POOL_LABEL_KEY).map(([value, labelKey]) => ({ value, label: t(labelKey) }))}
            />
          </Form.Item>
          <Form.Item
            name="hostname"
            label={t("nodes.hostnameLabel")}
            rules={[{ required: true }, { pattern: HOSTNAME_PATTERN }]}
          >
            <Input placeholder={t("nodes.hostnamePlaceholder")} />
          </Form.Item>
          <Form.Item name="note" label={t("nodes.noteLabel")}>
            <Input placeholder={t("nodes.notePlaceholder")} maxLength={128} />
          </Form.Item>
          <Form.Item name="nvme_devices" label={t("nodes.nvmeLabel")} extra={t("nodes.nvmeExtra")}>
            <Select mode="tags" placeholder={t("nodes.nvmePlaceholder")} open={false} />
          </Form.Item>
          <Form.Item name="ttl_hours" label={t("nodes.ttlLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} max={168} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      )}
    </Modal>
  );
}
