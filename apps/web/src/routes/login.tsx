/**
 * 登录/注册:AutoDL 式分屏 —— 左品牌渐变区(口号+卖点,lg 以下隐藏),右三态表单。
 * e2e 契约:placeholder「手机号」「短信验证码」、按钮「获取验证码」「注册并登录」、
 * Segmented「注册」exact 文本;页面不得出现第二个裸「注册」文本节点。
 */

import { CheckCircleOutlined } from "@ant-design/icons";
import type { TokenPair } from "@superdl/api-client";
import { brand, marketing } from "@superdl/ui";
import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { App, Button, Form, Grid, Input, Segmented, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import { useLogin, useRegister, useSendSmsCode } from "../api/mutations";
import { BrandLogo } from "../components/layout/BrandLogo";
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

const GRID_TEXTURE = `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'><path d='M40 0H0v40' fill='none' stroke='rgba(255,255,255,0.07)'/></svg>`,
)}")`;

function BrandPane() {
  return (
    <div
      style={{
        width: "45%",
        background: brand.heroBg,
        backgroundImage: GRID_TEXTURE,
        display: "flex",
        flexDirection: "column",
        padding: 40,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Link to="/" style={{ textDecoration: "none" }}>
          <BrandLogo variant="light" />
        </Link>
        <Link to="/" className="topbar-link">
          返回首页
        </Link>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
        <Typography.Title style={{ color: "#fff", fontSize: 36, marginBottom: 32 }}>
          {marketing.loginSlogan}
        </Typography.Title>
        <Space orientation="vertical" size={16}>
          {marketing.loginBullets.map((b) => (
            <Space key={b} size={10}>
              <CheckCircleOutlined style={{ color: "rgba(255,255,255,0.9)", fontSize: 16 }} />
              <span style={{ color: "rgba(255,255,255,0.9)", fontSize: 16 }}>{b}</span>
            </Space>
          ))}
        </Space>
      </div>
    </div>
  );
}

function LoginPage() {
  const navigate = useNavigate();
  const router = useRouter();
  const { redirect: redirectTo } = Route.useSearch();
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
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
    <div style={{ minHeight: "100vh", display: "flex" }}>
      {screens.lg && <BrandPane />}
      <div
        style={{
          flex: 1,
          background: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
        }}
      >
        <div style={{ width: 400 }}>
          {!screens.lg && (
            <div style={{ marginBottom: 24 }}>
              <Link to="/" style={{ textDecoration: "none" }}>
                <BrandLogo />
              </Link>
            </div>
          )}
          <Typography.Title level={3} style={{ marginTop: 0 }}>
            登录 SuperDL
          </Typography.Title>
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
              <Input prefix={<span style={{ color: "rgba(0,0,0,0.45)" }}>+86</span>} placeholder="手机号" maxLength={11} />
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
              size="large"
              loading={login.isPending || register.isPending}
            >
              {mode === "register" ? "注册并登录" : "登录"}
            </Button>
          </Form>
        </div>
      </div>
    </div>
  );
}
