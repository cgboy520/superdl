import { adminColors } from "@superdl/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { App, Button, Card, Form, Input, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useAdminLogin } from "../api";
import { useApiErrorText } from "../lib/apiError";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/login")({
  component: LoginPage,
});

function LoginPage() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const login = useAdminLogin({
    mutation: {
      onSuccess: (data) => {
        authStore.getState().login(data.access_token, data.admin);
        void navigate({ to: "/" });
      },
      onError: (e) => {
        message.error(errText(e, t("login.failed")));
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
        background: adminColors.bgBase,
      }}
    >
      <Card style={{ width: 380 }} title={t("app.title")}>
        <Form
          layout="vertical"
          onFinish={(values: { username: string; password: string }) =>
            login.mutate({ data: values })
          }
        >
          <Form.Item name="username" label={t("login.username")} rules={[{ required: true }]}>
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label={t("login.password")} rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={login.isPending}>
            {t("login.submit")}
          </Button>
          <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
            {t("login.isolatedNote")}
          </Typography.Paragraph>
        </Form>
      </Card>
    </div>
  );
}
