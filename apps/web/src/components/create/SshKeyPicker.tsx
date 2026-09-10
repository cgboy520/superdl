/** SSH 公钥选择块:查询失败可重试(绝不伪装成「你还没有密钥」)/ 无密钥时行内添加并自动选中 / 多选。
 *  开发机的 SSH 卡与服务的「同时开放 SSH」共用同一块 UI。 */

import { DataErrorAlert } from "@superdl/ui/components";
import { Alert, App, Button, Checkbox, Form, Input, Space } from "antd";
import { useTranslation } from "react-i18next";

import { useAddSshKey } from "../../api/mutations";
import { useSshKeys } from "../../api/queries";

export function SshKeyPicker({
  value,
  onChange,
}: {
  value: number[];
  onChange: (ids: number[]) => void;
}) {
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

  if (keysQ.isError) {
    return <DataErrorAlert onRetry={() => void keysQ.refetch()} />;
  }
  if (keys.length === 0) {
    return (
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Alert type="warning" showIcon title={t("copy.sshKeyOnly")} />
        <Form
          form={keyForm}
          layout="inline"
          onFinish={(v) => addKey.mutate({ name: v.name, public_key: v.public_key })}
        >
          <Form.Item name="name" rules={[{ required: true, message: t("create.keyNameRequired") }]}>
            <Input placeholder={t("create.keyNamePlaceholder")} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item
            name="public_key"
            rules={[{ required: true, message: t("create.keyContentRequired") }]}
            style={{ flex: 1 }}
          >
            <Input placeholder={t("create.keyPlaceholder")} />
          </Form.Item>
          <Form.Item>
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
