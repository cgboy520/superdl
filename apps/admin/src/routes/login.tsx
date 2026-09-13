import { adminColors, fontSize, space, TEST_IDS } from "@superdl/ui";
import { CopyButton, CopyField, LangSwitcher, Mono } from "@superdl/ui/components";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { App, Button, Card, Checkbox, Form, Input, QRCode, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminOut } from "@superdl/api-client";

import { useAdminLogin, useMfaSetupBegin, useMfaSetupConfirm, useMfaVerify } from "../api";
import { useApiErrorText } from "@superdl/ui";
import { authStore } from "../stores/auth";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): { returnTo?: string } => {
    // 仅接受站内路径(/ 开头且非 //)
    const r = search.returnTo;
    return { returnTo: typeof r === "string" && r.startsWith("/") && !r.startsWith("//") ? r : undefined };
  },
  component: LoginPage,
});

/** 登录落点:拿到正式 token 后写入并跳回原页。 */
function useFinishLogin() {
  const router = useRouter();
  const { returnTo } = Route.useSearch();
  return (accessToken: string, admin: AdminOut) => {
    authStore.getState().login(accessToken, admin);
    router.history.push(returnTo ?? "/");
  };
}

/** 二要素验证:6 位动态码或恢复码(XXXXX-XXXXX)。 */
function MfaVerifyForm({ ticket }: { ticket: string }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const finish = useFinishLogin();
  const [useRecovery, setUseRecovery] = useState(false);
  const [form] = Form.useForm<{ code: string }>();
  const verify = useMfaVerify({
    mutation: {
      onSuccess: (data) => {
        const left = data.recovery_codes_left;
        if (left != null && left <= 2) {
          message.warning(t("login.recoveryLowHint", { count: left }));
        }
        finish(data.access_token, data.admin);
      },
      onError: (e) => message.error(errText(e, t("login.failed"))),
    },
  });
  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={(v: { code: string }) => verify.mutate({ ticket, code: v.code.trim() })}
    >
      <Typography.Paragraph type="secondary">{t("login.mfaDesc")}</Typography.Paragraph>
      <Form.Item name="code" rules={[{ required: true, message: t("login.mfaCodeRequired") }]}>
        <Input
          placeholder={useRecovery ? t("login.mfaRecoveryPlaceholder") : t("login.mfaCodePlaceholder")}
          maxLength={useRecovery ? 11 : 6}
          autoComplete="one-time-code"
          autoFocus
        />
      </Form.Item>
      <Button type="primary" htmlType="submit" block loading={verify.isPending}>
        {t("login.mfaVerify")}
      </Button>
      <Button
        type="link"
        block
        onClick={() => {
          setUseRecovery((v) => !v);
          form.resetFields();
        }}
      >
        {useRecovery ? t("login.mfaUseTotp") : t("login.mfaUseRecovery")}
      </Button>
    </Form>
  );
}

/** 首次绑定:二维码 + 手动密钥 → 首个动态码确认 → 恢复码(仅此一次)。 */
function MfaSetupForm({ ticket }: { ticket: string }) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const finish = useFinishLogin();
  const [codes, setCodes] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);
  const begin = useMfaSetupBegin();
  const confirm = useMfaSetupConfirm({
    mutation: {
      onSuccess: (data) => setCodes(data.recovery_codes),
      onError: (e) => message.error(errText(e, t("login.failed"))),
    },
  });
  // 进入绑定步即取密钥(服务端复用进行中密钥)
  const { mutate: beginSetup } = begin;
  useEffect(() => {
    beginSetup({ ticket });
  }, [ticket, beginSetup]);

  if (codes != null) {
    return (
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t("login.recoveryDesc")}
        </Typography.Paragraph>
        <Card size="small">
          <CopyButton text={codes.join("\n")} label={t("login.recoveryCopy")} />
          <div style={{ marginTop: 8, fontSize: fontSize.caption, lineHeight: 1.8 }}>
            {codes.map((c) => (
              <Mono block key={c}>
                {c}
              </Mono>
            ))}
          </div>
        </Card>
        <Checkbox checked={saved} onChange={(e) => setSaved(e.target.checked)}>
          {t("login.recoveryConfirm")}
        </Checkbox>
        <Button
          type="primary"
          block
          disabled={!saved}
          onClick={() => {
            const data = confirm.data;
            if (data) finish(data.access_token, data.admin);
          }}
        >
          {t("login.recoveryContinue")}
        </Button>
      </Space>
    );
  }

  return (
    <Form layout="vertical" onFinish={(v: { code: string }) => confirm.mutate({ ticket, code: v.code.trim() })}>
      <Typography.Paragraph type="secondary">{t("login.mfaSetupDesc")}</Typography.Paragraph>
      <div style={{ textAlign: "center", marginBottom: 12 }}>
        {begin.data ? (
          <QRCode value={begin.data.otpauth_uri} size={168} />
        ) : begin.isError ? (
          <Space orientation="vertical" size={space.sm}>
            <Typography.Text type="danger">{errText(begin.error, t("login.failed"))}</Typography.Text>
            <Button size="small" onClick={() => beginSetup({ ticket })}>
              {t("common.retry", { ns: "shared" })}
            </Button>
          </Space>
        ) : (
          <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
        )}
      </div>
      {begin.data && (
        <Typography.Paragraph style={{ textAlign: "center" }}>
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("login.mfaManualKey")}
          </Typography.Text>
          <br />
          {/* 密钥是 testIds 白名单里的两处之一(同页还有别的 code) */}
          <CopyField value={begin.data.secret} code testId={TEST_IDS.mfaSecret} />
        </Typography.Paragraph>
      )}
      <Form.Item
        name="code"
        label={t("login.mfaConfirmLabel")}
        rules={[{ required: true, len: 6, message: t("login.mfaCodeRequired") }]}
      >
        <Input placeholder={t("login.mfaCodePlaceholder")} maxLength={6} autoComplete="one-time-code" />
      </Form.Item>
      <Button type="primary" htmlType="submit" block loading={confirm.isPending}>
        {t("login.mfaBind")}
      </Button>
    </Form>
  );
}

function LoginPage() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const finishLogin = useFinishLogin();
  const [challenge, setChallenge] = useState<{ status: "mfa_setup" | "mfa_required"; ticket: string } | null>(null);
  const login = useAdminLogin({
    mutation: {
      // MFA 开启时只回挑战票,关闭时直接拿 token
      onSuccess: (data) => {
        if (data.status === "ok") finishLogin(data.access_token, data.admin);
        else setChallenge({ status: data.status, ticket: data.ticket });
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
        position: "relative",
      }}
    >
      <div style={{ position: "absolute", top: 16, insetInlineEnd: 24 }}>
        <LangSwitcher variant="dark" />
      </div>
      <Card
        style={{ width: "min(380px, 92vw)" }}
        title={
          challenge == null
            ? t("app.title")
            : challenge.status === "mfa_setup"
              ? t("login.mfaSetupTitle")
              : t("login.mfaTitle")
        }
      >
        {challenge != null ? (
          <>
            {challenge.status === "mfa_setup" ? (
              <MfaSetupForm ticket={challenge.ticket} />
            ) : (
              <MfaVerifyForm ticket={challenge.ticket} />
            )}
            <Button type="link" block onClick={() => setChallenge(null)}>
              {t("login.backToLogin")}
            </Button>
          </>
        ) : (
          <Form
            layout="vertical"
            onFinish={(values: { username: string; password: string }) => login.mutate({ data: values })}
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
        )}
      </Card>
    </div>
  );
}
