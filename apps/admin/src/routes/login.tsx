import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button, Card, Form, Input, Typography, message } from "antd";

import { isApiError, useAdminLogin } from "../api";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/login")({
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const login = useAdminLogin({
    mutation: {
      onSuccess: (data) => {
        authStore.getState().login(data.access_token, data.admin);
        void navigate({ to: "/" });
      },
      onError: (e) => {
        message.error(isApiError(e) ? e.message : "登录失败");
      },
    },
  });

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0B1220",
      }}
    >
      <Card style={{ width: 380 }} title="SuperDL 管理控制台">
        <Form
          layout="vertical"
          onFinish={(values: { username: string; password: string }) =>
            login.mutate({ data: values })
          }
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={login.isPending}>
            登录
          </Button>
          <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
            管理端账号体系与用户端完全隔离,由平台管理员分配。
          </Typography.Paragraph>
        </Form>
      </Card>
    </div>
  );
}
