import type { TokenPair } from "@superdl/api-client";
import { colorPrimary } from "@superdl/ui";
import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { App, Button, Card, Form, Input, Segmented, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import { useLogin, useRegister, useSendSmsCode } from "../api/mutations";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): { redirect?: string } => {
    // 仅接受站内路径(/ 开头且非 //),防 open redirect
    const r = search.redirect;
    if (typeof r === "string" && r.startsWith("/") && !r.startsWith("//")) {
      return { redirect: r };
    }
    return {};
  },
  component: LoginPage,
});

type Mode = "sms" | "password" | "register";

function LoginPage() {
  const navigate = useNavigate();
  const router = useRouter();
  const { redirect: redirectTo } = Route.useSearch();
  const { message } = App.useApp();
  const [mode, setMode] = useState<Mode>("sms");
  const [countdown, setCountdown] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [form] = Form.useForm();

  useEffect(
    () => () => {
      if (timer.current) clearInterval(timer.current);
    },
    [],
  );

  const onLoggedIn = (data: unknown) => {
    const pair = data as TokenPair;
    authStore.getState().login(pair.access_token, pair.refresh_token);
    message.success("欢迎使用 SuperDL");
    if (redirectTo) {
      router.history.push(redirectTo);
    } else {
      void navigate({ to: "/instances" });
    }
  };

  const sendCode = useSendSmsCode({
    onSuccess: () => {
      message.success("验证码已发送(开发环境固定为 123456)");
      setCountdown(60);
      timer.current = setInterval(() => {
        setCountdown((c) => {
          if (c <= 1 && timer.current) clearInterval(timer.current);
          return c - 1;
        });
      }, 1000);
    },
  });
  const login = useLogin({ onSuccess: onLoggedIn });
  const register = useRegister({ onSuccess: onLoggedIn });

  const submit = (values: { phone: string; sms_code?: string; password?: string }) => {
    if (mode === "register") {
      register.mutate({
        phone: values.phone,
        sms_code: values.sms_code ?? "",
        password: values.password || null,
      });
    } else if (mode === "sms") {
      login.mutate({ phone: values.phone, sms_code: values.sms_code });
    } else {
      login.mutate({ phone: values.phone, password: values.password });
    }
  };

  const needsSms = mode !== "password";

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#F5F6FA",
      }}
    >
      <Card style={{ width: 400 }}>
        <Typography.Title level={3} style={{ color: colorPrimary, marginTop: 0 }}>
          SuperDL
        </Typography.Title>
        <Typography.Paragraph type="secondary">GPU 算力,即开即用</Typography.Paragraph>
        <Segmented
          block
          value={mode}
          onChange={(v) => setMode(v as Mode)}
          options={[
            { label: "验证码登录", value: "sms" },
            { label: "密码登录", value: "password" },
            { label: "注册", value: "register" },
          ]}
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item
            name="phone"
            rules={[{ required: true, pattern: /^1[3-9]\d{9}$/, message: "请输入正确的手机号" }]}
          >
            <Input placeholder="手机号" maxLength={11} />
          </Form.Item>
          {needsSms && (
            <Form.Item name="sms_code" rules={[{ required: true, message: "请输入验证码" }]}>
              <Space.Compact style={{ width: "100%" }}>
                <Input placeholder="短信验证码" maxLength={6} />
                <Button
                  disabled={countdown > 0}
                  loading={sendCode.isPending}
                  onClick={() => {
                    void form.validateFields(["phone"]).then(({ phone }) =>
                      sendCode.mutate({
                        phone,
                        purpose: mode === "register" ? "register" : "login",
                      }),
                    );
                  }}
                >
                  {countdown > 0 ? `${countdown}s` : "获取验证码"}
                </Button>
              </Space.Compact>
            </Form.Item>
          )}
          {mode !== "sms" && (
            <Form.Item
              name="password"
              rules={
                mode === "password"
                  ? [{ required: true, message: "请输入密码" }]
                  : [{ min: 8, message: "至少 8 位" }]
              }
            >
              <Input.Password
                placeholder={mode === "register" ? "设置密码(可选,至少 8 位)" : "密码"}
              />
            </Form.Item>
          )}
          <Button
            type="primary"
            htmlType="submit"
            block
            loading={login.isPending || register.isPending}
          >
            {mode === "register" ? "注册并登录" : "登录"}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
