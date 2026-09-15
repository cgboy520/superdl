/** 登录/注册页:验证码与密码登录、找回密码、字段标签及宽屏行情摘要。 */

import { CheckCircleOutlined } from "@ant-design/icons";
import type { SkuMarketOut, TokenPairOut } from "@superdl/api-client";
import { brand, compareAmounts, fontSize, space, useFormat } from "@superdl/ui";
import { LangSwitcher } from "@superdl/ui/components";
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
import { useSkus } from "../api/queries";
import { GRID_TEXTURE } from "../components/gridTexture";
import { BrandLogo } from "../components/layout/BrandLogo";
import { ThemeToggle } from "../components/layout/AppTopBar";
import { SmsCodeField } from "../components/SmsCodeField";
import { dedupAvailableTotal } from "../lib/inventory";
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
        /* ignored */
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
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].reduce((n, re) => n + (re.test(pw) ? 1 : 0), 0);
  if (pw.length >= 14 && classes >= 3) return "strong";
  if (pw.length >= 12 && classes >= 2) return "medium";
  return "weak";
}

/** 密码强度实时反馈(注册/重置模式):细进度条 + 分档文案 */
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

/** GPU 规格里的最低单卡时价(金额串比较,不过 float);无在售 GPU 规格时为 null。 */
function minGpuHourlyPrice(skus: readonly SkuMarketOut[]): string | null {
  let min: string | null = null;
  for (const s of skus) {
    if (!s.gpu_model) continue;
    if (min === null || compareAmounts(s.price_hourly, min) < 0) min = s.price_hourly;
  }
  return min;
}

/** 左侧品牌栏(lg+):行情摘要取 /skus 真实数据(最低时价 + 去重可开台数);加载中出骨架,失败回落静态三条。 */
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
  const [mode, setMode] = useState<Mode>(searchMode === "register" ? "register" : "sms");
  const [form] = Form.useForm();
  const watchedPassword = (Form.useWatch("password", form) as string | undefined) ?? "";

  const switchMode = (next: Mode) => {
    setMode(next);
    form.setFieldsValue({ sms_code: undefined, password: undefined, accept_terms: undefined });
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

  const submit = (values: { phone: string; sms_code?: string; password?: string; accept_terms?: boolean }) => {
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
          {(mode === "sms" || mode === "password") && (
            <Segmented
              block
              value={mode}
              onChange={(v) => switchMode(v as Mode)}
              options={[
                { label: t("login.modeSms"), value: "sms" },
                { label: t("login.modePassword"), value: "password" },
              ]}
              style={{ marginBottom: 16 }}
            />
          )}
          <Form form={form} layout="vertical" onFinish={submit}>
            <Form.Item
              name="phone"
              label={t("login.phoneLabel")}
              rules={[{ required: true, pattern: /^1[3-9]\d{9}$/, message: t("login.phoneInvalid") }]}
            >
              <Input
                prefix={<span style={{ color: token.colorTextSecondary }}>+86</span>}
                placeholder={t("login.phonePlaceholder")}
                maxLength={11}
                autoComplete="tel-national"
              />
            </Form.Item>
            {needsSms && (
              <SmsCodeField
                sms={sms}
                label={t("login.smsLabel")}
                placeholder={t("login.smsPlaceholder")}
                requiredMessage={t("login.smsRequired")}
                getCodeLabel={t("login.getCode")}
                onSend={() => {
                  void form.validateFields(["phone"]).then(({ phone }: { phone: string }) => sms.send(phone));
                }}
              />
            )}
            {mode !== "sms" && (
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
              {(mode === "sms" || mode === "password") && (
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
                  <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => switchMode("sms")}>
                    {t("login.goLogin")}
                  </Button>
                </Typography.Text>
              )}
            </span>
            <span>
              {(mode === "sms" || mode === "password") && (
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
