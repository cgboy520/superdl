/** SSH 公钥选择块:查询失败可重试(不伪装成「你还没有密钥」)/ 只有一把时自动选中 / 无密钥时行内添加(多行公钥框 + ssh-keygen 指引)并自动选中 / 多选。开发机 SSH 卡与服务「同时开放 SSH」共用。 */

import { controlWidth, fontSize, space } from "@superdl/ui";
import { DataErrorAlert } from "@superdl/ui/components";
import { App, Button, Checkbox, Form, Input, Space, Typography } from "antd";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useAddSshKey } from "../../api/mutations";
import { useSshKeys } from "../../api/queries";
import { CopyButton } from "../common";

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
  // 只有一把公钥时默认选中(一键创建的前提之一)
  const onlyKeyId = keys.length === 1 ? keys[0]!.id : undefined;
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
        {/* 怎么拿到公钥:给命令 + 复制,不只说「必须有」 */}
        <Space size={space.sm} wrap align="center">
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("create.keygenHint")}
          </Typography.Text>
          <Typography.Text code className="mono">
            {KEYGEN_CMD}
          </Typography.Text>
          <CopyButton text={KEYGEN_CMD} />
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
      onChange={(v) => onChange(v as number[])}
      options={keys.map((k) => ({
        value: k.id,
        label: `${k.name}(${k.fingerprint.slice(0, 20)}…)`,
      }))}
    />
  );
}
