/**
 * 登录/注册:左品牌渐变区(口号+卖点,lg 以下隐藏),右三态表单。
 * e2e 契约:placeholder「手机号」「短信验证码」、按钮「获取验证码」「注册并登录」、
 * Segmented「注册」exact 文本;页面不得出现第二个裸「注册」文本节点。
 */

import { CheckCircleOutlined } from "@ant-design/icons";
import type { TokenPair } from "@superdl/api-client";
import { brand } from "@superdl/ui";
import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { App, Button, Checkbox, Form, Grid, Input, Segmented, Space, Typography } from "antd";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { useLogin, useRegister, useResetPassword } from "../api/mutations";
import { BrandLogo } from "../components/layout/BrandLogo";
import { LangSwitcher } from "../components/layout/LangSwitcher";
import { useSmsCode } from "../lib/useSmsCode";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): { redirect?: string; mode?: "register" } => {
    // 回跳白名单:解析后必须仍属本站 origin(防 /\evil.com 这类绕过),归一化为 path+query+hash
    const out: { redirect?: string; mode?: "register" } = {};
    const r = search.redirect;
    if (typeof r === "string" && r !== "") {
      try {
        const u = new URL(r, window.location.origin);
        // origin 必须仍属本站;"/\evil.com" 这类会被 URL 解析归一成 "//evil.com",一并拒掉
        if (u.origin === window.location.origin && !u.pathname.startsWith("//")) {
          out.redirect = `${u.pathname}${u.search}${u.hash}`;
        }
      } catch {
        // 非法 redirect 直接丢弃
      }
    }
    // 「免费注册」CTA 直达注册态:默认短信登录态会让陌生人收到真验证码后被拒,
    // 再注册要重新要码(未消费码的指数退避已把等待翻倍)——转化漏斗最顶端
    if (search.mode === "register") out.mode = "register";
    return out;
  },
  component: LoginPage,
});

type Mode = "sms" | "password" | "register" | "reset";

const GRID_TEXTURE = `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'><path d='M40 0H0v40' fill='none' stroke='rgba(255,255,255,0.07)'/></svg>`,
)}")`;

function BrandPane() {
  const { t } = useTranslation();
  return (
    <div
      style={{
        width: "45%",
        // 网格纹理叠渐变(渐变即 background-image)
        backgroundImage: `${GRID_TEXTURE}, ${brand.heroBg}`,
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
          {t("login.backHome")}
        </Link>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
        <Typography.Title style={{ color: "#fff", fontSize: 36, marginBottom: 32 }}>
          {t("login.slogan")}
        </Typography.Title>
        <Space orientation="vertical" size={16}>
          {[t("login.bullets.b1"), t("login.bullets.b2"), t("login.bullets.b3")].map((b) => (
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
  const { t } = useTranslation();
  const navigate = useNavigate();
  const router = useRouter();
  const { redirect: redirectTo, mode: searchMode } = Route.useSearch();
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
  const [mode, setMode] = useState<Mode>(searchMode === "register" ? "register" : "sms");
  const [form] = Form.useForm();

  const onLoggedIn = (data: unknown) => {
    const pair = data as TokenPair;
    authStore.getState().login(pair.access_token, pair.refresh_token);
    if (redirectTo) {
      router.history.push(redirectTo);
    } else {
      void navigate({ to: "/instances" });
    }
  };

  const sms = useSmsCode(
    mode === "register" ? "register" : mode === "reset" ? "reset_password" : "login",
    t("login.codeSent"),
  );
  const login = useLogin({ onSuccess: onLoggedIn });
  const register = useRegister({ onSuccess: onLoggedIn });
  const resetPassword = useResetPassword({
    onSuccess: (data) => {
      message.success(t("login.resetDone"));
      onLoggedIn(data);
    },
  });

  const submit = (values: {
    phone: string;
    sms_code?: string;
    password?: string;
    accept_terms?: boolean;
  }) => {
    if (mode === "reset") {
      resetPassword.mutate({
        phone: values.phone,
        sms_code: values.sms_code ?? "",
        new_password: values.password ?? "",
      });
    } else if (mode === "register") {
      register.mutate({
        phone: values.phone,
        sms_code: values.sms_code ?? "",
        password: values.password || null,
        accept_terms: values.accept_terms === true,
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
          position: "relative",
        }}
      >
        <div style={{ position: "absolute", top: 16, right: 16 }}>
          <LangSwitcher variant="light" />
        </div>
        <div style={{ width: "100%", maxWidth: 400 }}>
          {!screens.lg && (
            <div style={{ marginBottom: 24 }}>
              <Link to="/" style={{ textDecoration: "none" }}>
                <BrandLogo />
              </Link>
            </div>
          )}
          <Typography.Title level={3} style={{ marginTop: 0 }}>
            {mode === "reset" ? t("login.resetTitle") : t("login.title")}
          </Typography.Title>
          {mode !== "reset" && (
            <Segmented
              block
              value={mode}
              onChange={(v) => setMode(v as Mode)}
              options={[
                { label: t("login.modeSms"), value: "sms" },
                { label: t("login.modePassword"), value: "password" },
                { label: t("login.modeRegister"), value: "register" },
              ]}
              style={{ marginBottom: 16 }}
            />
          )}
          <Form form={form} layout="vertical" onFinish={submit}>
            <Form.Item
              name="phone"
              rules={[{ required: true, pattern: /^1[3-9]\d{9}$/, message: t("login.phoneInvalid") }]}
            >
              <Input
                prefix={<span style={{ color: "rgba(0,0,0,0.45)" }}>+86</span>}
                placeholder={t("login.phonePlaceholder")}
                maxLength={11}
                autoComplete="tel-national"
                aria-label={t("login.phonePlaceholder")}
              />
            </Form.Item>
            {needsSms && (
              <Form.Item name="sms_code" rules={[{ required: true, message: t("login.smsRequired") }]}>
                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    placeholder={t("login.smsPlaceholder")}
                    maxLength={6}
                    autoComplete="one-time-code"
                    aria-label={t("login.smsPlaceholder")}
                  />
                  <Button
                    disabled={sms.countdown > 0}
                    loading={sms.sending}
                    onClick={() => {
                      void form.validateFields(["phone"]).then(({ phone }) => sms.send(phone));
                    }}
                  >
                    {sms.countdown > 0 ? `${sms.countdown}s` : t("login.getCode")}
                  </Button>
                </Space.Compact>
              </Form.Item>
            )}
            {mode !== "sms" && (
              <Form.Item
                name="password"
                rules={
                  mode === "password"
                    ? [{ required: true, message: t("login.passwordRequired") }]
                    : mode === "reset"
                      ? [{ required: true, min: 12, message: t("login.passwordMin") }]
                      : [{ min: 12, message: t("login.passwordMin") }]
                }
              >
                <Input.Password
                  autoComplete={mode === "password" ? "current-password" : "new-password"}
                  aria-label={
                    mode === "password"
                      ? t("login.passwordPlaceholder")
                      : mode === "reset"
                        ? t("login.passwordResetPlaceholder")
                        : t("login.passwordSetPlaceholder")
                  }
                  placeholder={
                    mode === "password"
                      ? t("login.passwordPlaceholder")
                      : mode === "reset"
                        ? t("login.passwordResetPlaceholder")
                        : t("login.passwordSetPlaceholder")
                  }
                />
              </Form.Item>
            )}
            {mode === "register" && (
              <Form.Item
                name="accept_terms"
                valuePropName="checked"
                rules={[
                  {
                    validator: (_, v) =>
                      v === true
                        ? Promise.resolve()
                        : Promise.reject(new Error(t("login.termsRequired"))),
                  },
                ]}
              >
                <Checkbox>
                  <Trans
                    i18nKey="login.terms"
                    components={{
                      terms: <a href="/legal/terms" target="_blank" rel="noreferrer" />,
                      privacy: <a href="/legal/privacy" target="_blank" rel="noreferrer" />,
                    }}
                  />
                </Checkbox>
              </Form.Item>
            )}
            <Button
              type="primary"
              htmlType="submit"
              block
              size="large"
              loading={login.isPending || register.isPending || resetPassword.isPending}
            >
              {mode === "register"
                ? t("login.submitRegister")
                : mode === "reset"
                  ? t("login.submitReset")
                  : t("login.submitLogin")}
            </Button>
          </Form>
          <div style={{ marginTop: 12, textAlign: "right" }}>
            {mode === "password" && (
              <Typography.Link onClick={() => setMode("reset")}>
                {t("login.forgotPassword")}
              </Typography.Link>
            )}
            {mode === "reset" && (
              <Typography.Link onClick={() => setMode("password")}>
                {t("login.backToLogin")}
              </Typography.Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
