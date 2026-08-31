/**
 * 登录/注册:左品牌渐变区(口号+卖点,lg 以下隐藏),右三态表单。
 * e2e 契约:placeholder「手机号」「短信验证码」、按钮「获取验证码」「注册并登录」、
 * Segmented「注册」exact 文本;页面不得出现第二个裸「注册」文本节点。
 */

import { CheckCircleOutlined } from "@ant-design/icons";
import type { TokenPairOut } from "@superdl/api-client";
import { brand, fontSize } from "@superdl/ui";
import { LangSwitcher } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import { App, Button, Checkbox, Form, Grid, Input, Progress, Segmented, Space, theme, Typography } from "antd";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { useLogin, useRegister, useResetPassword } from "../api/mutations";
import { GRID_TEXTURE } from "../components/gridTexture";
import { BrandLogo } from "../components/layout/BrandLogo";
import { ThemeToggle } from "../components/layout/AppTopBar";
import { useSmsCode } from "../lib/useSmsCode";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): { redirect?: string; mode?: "register" } => {
    const out: { redirect?: string; mode?: "register" } = {};
    const r = search.redirect;
    if (typeof r === "string" && r !== "") {
      try {
        const u = new URL(r, window.location.origin);
        if (u.origin === window.location.origin && !u.pathname.startsWith("//")) {
          out.redirect = `${u.pathname}${u.search}${u.hash}`;
        }
      } catch {
        // 非法 redirect 直接丢弃
      }
    }
    if (search.mode === "register") out.mode = "register";
    return out;
  },
  component: LoginPage,
});

type Mode = "sms" | "password" | "register" | "reset";

/** 密码强度三档:弱=仅满足长度;中=≥12 位且含两类字符;强=≥14 位且含三类字符 */
type PasswordStrength = "weak" | "medium" | "strong";

function passwordStrengthOf(pw: string): PasswordStrength {
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].reduce(
    (n, re) => n + (re.test(pw) ? 1 : 0),
    0,
  );
  if (pw.length >= 14 && classes >= 3) return "strong";
  if (pw.length >= 12 && classes >= 2) return "medium";
  return "weak";
}

/** 密码强度实时反馈(注册/重置模式):细进度条 + 分档文案,颜色走 antd token */
function PasswordStrengthHint({ password }: { password: string }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const level = passwordStrengthOf(password);
  const meta = {
    weak: { percent: 34, color: token.colorError, label: t("login.passwordStrengthWeak") },
    medium: { percent: 67, color: token.colorWarning, label: t("login.passwordStrengthMedium") },
    strong: { percent: 100, color: token.colorSuccess, label: t("login.passwordStrengthStrong") },
  }[level];
  return (
    <Space size={8} style={{ width: "100%" }}>
      <Progress
        percent={meta.percent}
        showInfo={false}
        strokeColor={meta.color}
        size="small"
        style={{ flex: 1, margin: 0 }}
      />
      <Typography.Text style={{ color: meta.color, fontSize: fontSize.caption, whiteSpace: "nowrap" }}>
        {meta.label}
      </Typography.Text>
    </Space>
  );
}

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
        <Typography.Title style={{ color: "#fff", fontSize: fontSize.kpi, marginBottom: 32 }}>
          {t("login.slogan")}
        </Typography.Title>
        <Space orientation="vertical" size={16}>
          {[t("login.bullets.b1"), t("login.bullets.b2"), t("login.bullets.b3")].map((b) => (
            <Space key={b} size={10}>
              <CheckCircleOutlined
                style={{ color: "rgba(255,255,255,0.9)", fontSize: fontSize.sectionTitle }}
              />
              <span style={{ color: "rgba(255,255,255,0.9)", fontSize: fontSize.sectionTitle }}>
                {b}
              </span>
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
  const { token } = theme.useToken();
  const [mode, setMode] = useState<Mode>(searchMode === "register" ? "register" : "sms");
  const [form] = Form.useForm();
  const watchedPassword: string = Form.useWatch("password", form) ?? "";

  const switchMode = (next: Mode) => {
    setMode(next);
    form.setFieldsValue({ sms_code: undefined, password: undefined, accept_terms: undefined });
  };

  const onLoggedIn = (data: unknown) => {
    const pair = data as TokenPairOut;
    // refresh token 已由服务端经 HttpOnly Cookie 下发,JS 只留 access token
    authStore.getState().login(pair.access_token);
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
          // 容器底色走 token(暗色主题为深靛灰)
          background: token.colorBgContainer,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          position: "relative",
        }}
      >
        <div style={{ position: "absolute", top: 16, right: 16 }}>
          <Space size={4}>
            <ThemeToggle variant="plain" />
            <LangSwitcher variant="light" />
          </Space>
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
              onChange={(v) => switchMode(v as Mode)}
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
                prefix={<span style={{ color: token.colorTextSecondary }}>+86</span>}
                placeholder={t("login.phonePlaceholder")}
                maxLength={11}
                autoComplete="tel-national"
                aria-label={t("login.phonePlaceholder")}
              />
            </Form.Item>
            {needsSms && (
              // 校验挂内层 Form.Item(唯一控件是 Input):挂外层会把 id/aria-required 注到
              // Space.Compact 的 div 上,div 不支持该 ARIA 属性(axe aria-allowed-attr,critical)
              <Form.Item>
                <Space.Compact style={{ width: "100%", alignItems: "flex-start" }}>
                  <Form.Item
                    name="sms_code"
                    rules={[{ required: true, message: t("login.smsRequired") }]}
                    style={{ flex: 1, marginBottom: 0 }}
                  >
                    <Input
                      placeholder={t("login.smsPlaceholder")}
                      maxLength={6}
                      autoComplete="one-time-code"
                      aria-label={t("login.smsPlaceholder")}
                    />
                  </Form.Item>
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
                {...(mode !== "password" && watchedPassword !== ""
                  ? {
                      // 强度实时反馈挂在 extra:不占校验错误位
                      extra: <PasswordStrengthHint password={watchedPassword} />,
                    }
                  : {})}
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
              <Button type="link" size="small" onClick={() => switchMode("reset")}>
                {t("login.forgotPassword")}
              </Button>
            )}
            {mode === "reset" && (
              <Button type="link" size="small" onClick={() => switchMode("password")}>
                {t("login.backToLogin")}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
