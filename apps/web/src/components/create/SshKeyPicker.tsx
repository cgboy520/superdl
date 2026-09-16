/** SSH public key block: a failed query is retryable (never disguised as "you have no keys yet") / a single key is auto-selected / without keys inline add (multi-line key box + ssh-keygen hint) and auto-select / multi-select. Shared by the dev-box SSH card and the service "also enable SSH". */

import { controlWidth, fontSize, space } from "@superdl/ui";
import { CopyField, DataErrorAlert } from "@superdl/ui/components";
import { App, Button, Checkbox, Form, Input, Space, Typography } from "antd";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useAddSshKey } from "../../api/mutations";
import { useSshKeys } from "../../api/queries";

const KEYGEN_CMD = 'ssh-keygen -t ed25519 -C "you@example.com"';

export function SshKeyPicker({ value, onChange }: { value: number[]; onChange: (ids: number[]) => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const keysQ = useSshKeys();
  const keys = keysQ.data ?? [];
  const [keyForm] = Form.useForm<{ name: string; public_key: string }>();
  const addKey = useAddSshKey({
    onSuccess: (key) => {
      message.success(t("create.keyAdded"));
      keyForm.resetFields();
      if (!value.includes(key.id)) onChange([...value, key.id]);
    },
  });
  const firstKey = keys[0];
  const onlyKeyId = keys.length === 1 && firstKey ? firstKey.id : undefined;
  useEffect(() => {
    if (onlyKeyId !== undefined && value.length === 0) onChange([onlyKeyId]);
  }, [onlyKeyId, value.length, onChange]);

  if (keysQ.isError) {
    return <DataErrorAlert onRetry={() => void keysQ.refetch()} />;
  }
  if (keys.length === 0) {
    return (
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>
        <Space size={space.sm} wrap align="center">
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("create.keygenHint")}
          </Typography.Text>
          <CopyField value={KEYGEN_CMD} code />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("create.keygenWhere")}
          </Typography.Text>
        </Space>
        <Form
          form={keyForm}
          layout="vertical"
          style={{ maxWidth: 640 }}
          onFinish={(v) => addKey.mutate({ name: v.name, public_key: v.public_key.trim() })}
        >
          <Form.Item
            name="name"
            label={t("create.keyNamePlaceholder")}
            rules={[{ required: true, message: t("create.keyNameRequired") }]}
          >
            <Input placeholder={t("create.keyNamePlaceholder")} style={{ width: controlWidth.md }} maxLength={64} />
          </Form.Item>
          <Form.Item
            name="public_key"
            label={t("create.keyPlaceholder")}
            rules={[{ required: true, message: t("create.keyContentRequired") }]}
          >
            <Input.TextArea
              placeholder="ssh-ed25519 AAAA… you@example.com"
              autoSize={{ minRows: 2, maxRows: 4 }}
              className="mono"
              style={{ fontSize: fontSize.caption }}
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={addKey.isPending}>
              {t("create.addKey")}
            </Button>
          </Form.Item>
        </Form>
      </Space>
    );
  }
  return (
    <Checkbox.Group
      value={value}
      onChange={(v) => onChange(v)}
      options={keys.map((k) => ({
        value: k.id,
        label: `${k.name}(${k.fingerprint.slice(0, 20)}…)`,
      }))}
    />
  );
}
