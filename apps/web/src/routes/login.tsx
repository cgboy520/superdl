/** Sign-in / sign-up page: code or password sign-in by email or phone handle, email-first sign-up
 *  (phone only when the compliance profile requires it), password reset, live market summary. */

import { CheckCircleOutlined } from "@ant-design/icons";
import type { SkuMarketOut, TokenPairOut } from "@superdl/api-client";
import { brand, compareAmounts, fontSize, space, useFormat } from "@superdl/ui";
import { LangSwitcher, PhoneField } from "@superdl/ui/components";
import { createFileRoute, Link, useNavigate, useRouter } from "@tanstack/react-router";
import {
  App,
  Button,
  Checkbox,
  Form,
  Grid,
  Input,
  Progress,
  Segmented,
  Skeleton,
  Space,
  theme,
  Typography,
} from "antd";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { useLogin, useRegister, useResetPassword } from "../api/mutations";
import { useSiteConfig, useSkus } from "../api/queries";
import { GRID_TEXTURE } from "../components/gridTexture";
import { BrandLogo } from "../components/layout/BrandLogo";
import { ThemeToggle } from "../components/layout/AppTopBar";
import { CodeField } from "../components/CodeField";
import { dedupAvailableTotal } from "../lib/inventory";
import { useVerificationCode } from "../lib/useVerificationCode";
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
        /* ignored */
      }
    }
    if (search.mode === "register") out.mode = "register";
    return out;
  },
  component: LoginPage,
});

type Mode = "code" | "password" | "register" | "reset";

/** Three password strength tiers: weak = length only; medium = ≥12 characters with two character classes; strong = ≥14 with three */
type PasswordStrength = "weak" | "medium" | "strong";

function passwordStrengthOf(pw: string): PasswordStrength {
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].reduce((n, re) => n + (re.test(pw) ? 1 : 0), 0);
  if (pw.length >= 14 && classes >= 3) return "strong";
  if (pw.length >= 12 && classes >= 2) return "medium";
  return "weak";
}

/** Live password strength feedback (register / reset modes): thin progress bar + tier copy */
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
    <Space size={space.sm} style={{ width: "100%" }}>
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

/** Lowest per-card hourly price among GPU specs (amount-string comparison, no float); null without GPU specs on sale. */
function minGpuHourlyPrice(skus: readonly SkuMarketOut[]): string | null {
  let min: string | null = null;
  for (const s of skus) {
    if (!s.gpu_model) continue;
    if (min === null || compareAmounts(s.price_hourly, min) < 0) min = s.price_hourly;
  }
  return min;
}

/** Left brand column (lg+): the market summary uses real /skus data (lowest hourly price + deduplicated available count); skeleton while loading, three static facts on failure. */
function BrandPane() {
  const { t } = useTranslation();
  const { formatHourlyPrice } = useFormat();
  const { data: skus, isLoading, isError } = useSkus();
  const minPrice = minGpuHourlyPrice(skus ?? []);
  const live =
    !isError && minPrice !== null
      ? [
          t("login.liveMinPrice", { price: formatHourlyPrice(minPrice) }),
          t("login.liveStock", { count: dedupAvailableTotal(skus ?? []) }),
          t("login.bullets.b1"),
        ]
      : null;
  const facts = live ?? [t("login.bullets.b1"), t("login.bullets.b2"), t("login.bullets.b3")];
  return (
    <div
      style={{
        width: "45%",
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
        <Typography.Title style={{ color: brand.onHero, fontSize: fontSize.kpi, marginBottom: space.xxl }}>
          {t("login.slogan")}
        </Typography.Title>
        {isLoading ? (
          <Space orientation="vertical" size={space.lg}>
            {[1, 2, 3].map((i) => (
              <Skeleton.Input key={i} active size="small" />
            ))}
          </Space>
        ) : (
          <Space orientation="vertical" size={space.lg}>
            {facts.map((b) => (
              <Space key={b} size={space.md}>
                <CheckCircleOutlined style={{ color: "rgba(255,255,255,0.9)", fontSize: fontSize.sectionTitle }} />
                <span style={{ color: "rgba(255,255,255,0.9)", fontSize: fontSize.sectionTitle }}>{b}</span>
              </Space>
            ))}
          </Space>
        )}
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
  const { data: site } = useSiteConfig();
  const phoneRequired = site?.phone_required ?? false;
  const dialCodes = site?.phone_dial_codes ?? [];
  const [mode, setMode] = useState<Mode>(searchMode === "register" ? "register" : "code");
  const [form] = Form.useForm();
  const watchedPassword = (Form.useWatch("password", form) as string | undefined) ?? "";

  const switchMode = (next: Mode) => {
    setMode(next);
    form.setFieldsValue({
      code: undefined,
      email_code: undefined,
      phone_code: undefined,
      password: undefined,
      accept_terms: undefined,
    });
  };

  const onLoggedIn = (data: unknown) => {
    const pair = data as TokenPairOut;
    authStore.getState().login(pair.access_token);
    if (redirectTo) {
      router.history.push(redirectTo);
    } else {
      void navigate({ to: "/instances" });
    }
  };

  const codes = useVerificationCode(
    mode === "register" ? "register" : mode === "reset" ? "reset_password" : "login",
    t("login.codeSent"),
  );
  const phoneCodes = useVerificationCode("register", t("login.codeSent"));
  const login = useLogin({ onSuccess: onLoggedIn });
  const register = useRegister({ onSuccess: onLoggedIn });
  const resetPassword = useResetPassword({
    onSuccess: (data) => {
      message.success(t("login.resetDone"));
      onLoggedIn(data);
    },
  });

  const submit = (values: {
    handle?: string;
    email?: string;
    email_code?: string;
    phone?: string;
    phone_code?: string;
    code?: string;
    password?: string;
    accept_terms?: boolean;
  }) => {
    if (mode === "reset") {
      resetPassword.mutate({
        handle: values.handle ?? "",
        code: values.code ?? "",
        new_password: values.password ?? "",
      });
    } else if (mode === "register") {
      register.mutate({
        email: values.email ?? "",
        email_code: values.email_code ?? "",
        password: values.password || null,
        accept_terms: values.accept_terms === true,
        ...(phoneRequired ? { phone: values.phone ?? null, phone_code: values.phone_code ?? null } : {}),
      });
    } else if (mode === "code") {
      login.mutate({ handle: values.handle ?? "", code: values.code });
    } else {
      login.mutate({ handle: values.handle ?? "", password: values.password });
    }
  };

  const handleRule = {
    required: true,
    pattern: /^([^@\s]+@[^@\s]+\.[^@\s]+|\+[1-9]\d{6,14})$/,
    message: t("login.handleInvalid"),
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex" }}>
      {screens.lg && <BrandPane />}
      <div
        style={{
          flex: 1,
          background: token.colorBgContainer,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 24,
          position: "relative",
        }}
      >
        <div style={{ position: "absolute", top: 16, right: 16 }}>
          <Space size={space.xs}>
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
            {mode === "reset"
              ? t("login.resetTitle")
              : mode === "register"
                ? t("login.registerTitle")
                : t("login.title")}
          </Typography.Title>
          {(mode === "code" || mode === "password") && (
            <Segmented
              block
              value={mode}
              onChange={(v) => switchMode(v as Mode)}
              options={[
                { label: t("login.modeCode"), value: "code" },
                { label: t("login.modePassword"), value: "password" },
              ]}
              style={{ marginBottom: 16 }}
            />
          )}
          <Form form={form} layout="vertical" onFinish={submit}>
            {mode === "register" ? (
              <>
                <Form.Item
                  name="email"
                  label={t("login.emailLabel")}
                  rules={[{ required: true, pattern: /^[^@\s]+@[^@\s]+\.[^@\s]+$/, message: t("login.emailInvalid") }]}
                >
                  <Input
                    placeholder={t("login.emailPlaceholder")}
                    maxLength={254}
                    autoComplete="email"
                    inputMode="email"
                  />
                </Form.Item>
                <CodeField
                  code={codes}
                  name="email_code"
                  label={t("login.emailCodeLabel")}
                  placeholder={t("login.emailCodePlaceholder")}
                  requiredMessage={t("login.codeRequired")}
                  getCodeLabel={t("login.getCode")}
                  onSend={() => {
                    void form.validateFields(["email"]).then(({ email }: { email: string }) => codes.send(email));
                  }}
                />
                {phoneRequired && (
                  <>
                    <Form.Item
                      name="phone"
                      label={t("login.phoneLabel")}
                      extra={t("login.phoneRequiredHint")}
                      rules={[{ required: true, message: t("login.phoneInvalid") }]}
                    >
                      <PhoneField dialCodes={dialCodes} placeholder={t("login.phonePlaceholder")} />
                    </Form.Item>
                    <CodeField
                      code={phoneCodes}
                      name="phone_code"
                      label={t("login.smsLabel")}
                      placeholder={t("login.smsPlaceholder")}
                      requiredMessage={t("login.codeRequired")}
                      getCodeLabel={t("login.getCode")}
                      onSend={() => {
                        void form
                          .validateFields(["phone"])
                          .then(({ phone }: { phone: string }) => phoneCodes.send(phone));
                      }}
                    />
                  </>
                )}
              </>
            ) : (
              <Form.Item name="handle" label={t("login.handleLabel")} rules={[handleRule]}>
                <Input placeholder={t("login.handlePlaceholder")} maxLength={254} autoComplete="username" />
              </Form.Item>
            )}
            {(mode === "code" || mode === "reset") && (
              <CodeField
                code={codes}
                label={t("login.codeLabel")}
                placeholder={t("login.codePlaceholder")}
                requiredMessage={t("login.codeRequired")}
                getCodeLabel={t("login.getCode")}
                onSend={() => {
                  void form.validateFields(["handle"]).then(({ handle }: { handle: string }) => codes.send(handle));
                }}
              />
            )}
            {mode !== "code" && (
              <Form.Item
                name="password"
                label={
                  mode === "password"
                    ? t("login.passwordLabel")
                    : mode === "reset"
                      ? t("login.passwordNewLabel")
                      : t("login.passwordSetLabel")
                }
                rules={
                  mode === "password"
                    ? [{ required: true, message: t("login.passwordRequired") }]
                    : mode === "reset"
                      ? [{ required: true, min: 12, message: t("login.passwordMin") }]
                      : [{ min: 12, message: t("login.passwordMin") }]
                }
                {...(mode !== "password" && watchedPassword !== ""
                  ? {
                      extra: <PasswordStrengthHint password={watchedPassword} />,
                    }
                  : {})}
              >
                <Input.Password
                  autoComplete={mode === "password" ? "current-password" : "new-password"}
                  placeholder={
                    mode === "password"
                      ? undefined
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
                      v === true ? Promise.resolve() : Promise.reject(new Error(t("login.termsRequired"))),
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
          <div
            style={{
              marginTop: 12,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 8,
            }}
          >
            <span>
              {(mode === "code" || mode === "password") && (
                <Typography.Text type="secondary">
                  {t("login.noAccount")}{" "}
                  <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => switchMode("register")}>
                    {t("login.goRegister")}
                  </Button>
                </Typography.Text>
              )}
              {mode === "register" && (
                <Typography.Text type="secondary">
                  {t("login.hasAccount")}{" "}
                  <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => switchMode("code")}>
                    {t("login.goLogin")}
                  </Button>
                </Typography.Text>
              )}
            </span>
            <span>
              {(mode === "code" || mode === "password") && (
                <Button type="link" size="small" onClick={() => switchMode("reset")}>
                  {t("login.forgotPassword")}
                </Button>
              )}
              {mode === "reset" && (
                <Button type="link" size="small" onClick={() => switchMode("password")}>
                  {t("login.backToLogin")}
                </Button>
              )}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
