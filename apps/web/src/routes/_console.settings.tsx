/** 账户设置:SSH 公钥管理 / 通知阈值(保存按钮) / 账号(实名预留+登出)。 */

import { TableErrorEmpty } from "../components/QueryState";
import { formatDateTime } from "@superdl/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
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
  Tag,
  Typography,
} from "antd";
import { useState } from "react";

import {
  useAddSshKey,
  useDeleteSshKey,
  useSetWarnThreshold,
  useSubmitRealName,
} from "../api/mutations";
import { useMe, useSshKeys } from "../api/queries";
import { requireAuth } from "../lib/guard";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/_console/settings")({
  beforeLoad: requireAuth,
  component: SettingsPage,
});

function SettingsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const { data: keys, isLoading, isError, refetch } = useSshKeys();
  const [form] = Form.useForm();
  const [warnHours, setWarnHours] = useState<number>();

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

      <Card
        id="ssh"
        title="SSH 公钥"
        extra={<Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>}
      >
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <Table
            rowKey="id"
            size="small"
            loading={isLoading}
            pagination={false}
            scroll={{ x: 640 }}
            dataSource={keys ?? []}
            locale={{
              emptyText: isError ? (
                <TableErrorEmpty onRetry={() => void refetch()} />
              ) : (
                "还没有公钥,先在下方添加(ssh-keygen -t ed25519 生成)"
              ),
            }}
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
            value={warnHours ?? me?.low_balance_warn_hours}
            onChange={(v) => setWarnHours(v ?? undefined)}
            onPressEnter={() => {
              const v = warnHours ?? me?.low_balance_warn_hours;
              if (v != null) setThreshold.mutate(v);
            }}
          />
          <Button
            loading={setThreshold.isPending}
            onClick={() => {
              const v = warnHours ?? me?.low_balance_warn_hours;
              if (v != null) setThreshold.mutate(v);
            }}
          >
            保存
          </Button>
          <Typography.Text type="secondary">预计可用时长低于该值时提醒</Typography.Text>
        </Space>
      </Card>

      <RealNameCard verified={me?.verification_status === "verified"} />

      <Card title="账号">
        <Space orientation="vertical" size={12}>
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

function RealNameCard({ verified }: { verified: boolean }) {
  const { message } = App.useApp();
  const [form] = Form.useForm<{ name: string; id_number: string }>();
  const submit = useSubmitRealName({
    onSuccess: () => message.success("实名认证已完成"),
  });
  return (
    <Card
      title={
        <Space size={8}>
          实名认证
          <Tag color={verified ? "green" : "orange"}>{verified ? "已认证" : "未认证"}</Tag>
        </Space>
      }
    >
      {verified ? (
        <Typography.Text type="secondary">已完成实名认证,信息仅存脱敏形态。</Typography.Text>
      ) : (
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            按监管要求完成三要素核验(姓名 + 身份证号 + 账号手机号);信息仅用于核验,身份证号只保存脱敏形态。
          </Typography.Text>
          <Form
            form={form}
            layout="inline"
            onFinish={(v) => submit.mutate({ name: v.name, id_number: v.id_number })}
          >
            <Form.Item
              name="name"
              rules={[{ required: true, min: 2, message: "请输入与身份证一致的姓名" }]}
            >
              <Input placeholder="真实姓名" style={{ width: 160 }} />
            </Form.Item>
            <Form.Item
              name="id_number"
              rules={[
                {
                  required: true,
                  pattern: /^\d{17}[\dXx]$/,
                  message: "请输入 18 位身份证号",
                },
              ]}
            >
              <Input placeholder="身份证号(18 位)" style={{ width: 220 }} maxLength={18} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={submit.isPending}>
              提交核验
            </Button>
          </Form>
        </Space>
      )}
    </Card>
  );
}
