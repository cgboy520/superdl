/** 账户设置:SSH 公钥管理 / 通知阈值 / 登出。 */

import { copy, formatDateTime } from "@superdl/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Space,
  Table,
  Typography,
} from "antd";

import { useAddSshKey, useDeleteSshKey, useSetWarnThreshold } from "../api/mutations";
import { useMe, useSshKeys } from "../api/queries";
import { requireAuth } from "../lib/guard";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/_console/settings")({
  beforeLoad: requireAuth,
  component: SettingsPage,
});

function SettingsPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const { data: keys, isLoading } = useSshKeys();
  const [form] = Form.useForm();

  const addKey = useAddSshKey({
    onSuccess: () => {
      message.success("公钥已添加");
      form.resetFields();
    },
  });
  const delKey = useDeleteSshKey();
  const setThreshold = useSetWarnThreshold({ onSuccess: () => message.success("已保存") });

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        账户设置
      </Typography.Title>

      <Card title="SSH 公钥" extra={<Typography.Text type="secondary">{copy.sshKeyOnly}</Typography.Text>}>
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Table
            rowKey="id"
            size="small"
            loading={isLoading}
            pagination={false}
            dataSource={keys ?? []}
            locale={{ emptyText: "还没有公钥,先在下方添加(ssh-keygen -t ed25519 生成)" }}
            columns={[
              { title: "名称", dataIndex: "name" },
              {
                title: "指纹",
                render: (_, r) => <Typography.Text code>{r.fingerprint}</Typography.Text>,
              },
              { title: "添加时间", render: (_, r) => formatDateTime(r.created_at) },
              {
                title: "操作",
                render: (_, r) => (
                  <Popconfirm
                    title="删除该公钥?已创建实例内的 authorized_keys 不受影响"
                    onConfirm={() => delKey.mutate(r.id)}
                  >
                    <Button size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                ),
              },
            ]}
          />
          <Form
            form={form}
            layout="vertical"
            onFinish={(v: { name: string; public_key: string }) => addKey.mutate(v)}
          >
            <Form.Item
              name="name"
              label="名称"
              rules={[{ required: true, message: "如:办公电脑" }]}
            >
              <Input style={{ width: 240 }} maxLength={64} />
            </Form.Item>
            <Form.Item
              name="public_key"
              label="公钥内容"
              rules={[
                { required: true, message: "请粘贴公钥" },
                {
                  pattern: /^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521))\s+\S+/,
                  message: "格式:ssh-ed25519 AAAA…(支持 ed25519/rsa/ecdsa)",
                },
              ]}
            >
              <Input.TextArea
                rows={3}
                placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5… you@host"
              />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={addKey.isPending}>
              添加公钥
            </Button>
          </Form>
        </Space>
      </Card>

      <Card title="通知">
        <Space>
          <Typography.Text>低余额预警阈值(小时)</Typography.Text>
          <InputNumber
            min={1}
            max={168}
            defaultValue={me?.low_balance_warn_hours}
            onPressEnter={(e) => setThreshold.mutate(Number((e.target as HTMLInputElement).value))}
          />
          <Typography.Text type="secondary">回车保存;预计可用时长低于该值时提醒</Typography.Text>
        </Space>
      </Card>

      <Card title="账号">
        <Space orientation="vertical">
          <Typography.Text>手机号:{me?.phone}</Typography.Text>
          <Button
            danger
            onClick={() => {
              authStore.getState().logout();
              void navigate({ to: "/login" });
            }}
          >
            退出登录
          </Button>
        </Space>
      </Card>
    </Space>
  );
}
