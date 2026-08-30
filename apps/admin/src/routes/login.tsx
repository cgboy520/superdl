import { adminColors, fontSize } from "@superdl/ui";
import { LangSwitcher } from "@superdl/ui/components";
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
    // 仅接受站内路径(/ 开头且非 //),防 open redirect
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
      {/* 切换码型清空已输入:6 位动态码残留进恢复码框(或反之)必校验失败,还是用户的错 */}
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
  const { t } = useTranslation();
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
  // 进入绑定步即取密钥,服务端复用进行中密钥,StrictMode 重入/刷新二维码不变
  const { mutate: beginSetup } = begin;
  useEffect(() => {
    beginSetup({ ticket });
  }, [ticket, beginSetup]);

  if (codes != null) {
    return (
      <Space orientation="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t("login.recoveryDesc")}
        </Typography.Paragraph>
        <Card size="small">
          <Typography.Text code copyable={{ text: codes.join("\n") }}>
            {t("login.recoveryCopy")}
          </Typography.Text>
          <pre style={{ margin: "8px 0 0", fontSize: fontSize.caption, lineHeight: 1.8 }}>{codes.join("\n")}</pre>
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
    <Form
      layout="vertical"
      onFinish={(v: { code: string }) => confirm.mutate({ ticket, code: v.code.trim() })}
    >
      <Typography.Paragraph type="secondary">{t("login.mfaSetupDesc")}</Typography.Paragraph>
      <div style={{ textAlign: "center", marginBottom: 12 }}>
        {begin.data ? (
          <QRCode value={begin.data.otpauth_uri} size={168} />
        ) : begin.isError ? (
          // 失败必明示(全站纪律):密钥下发失败给重试,不能停在永久「加载中」
          <Space orientation="vertical" size={8}>
            <Typography.Text type="danger">{errText(begin.error, t("login.failed"))}</Typography.Text>
            <Button size="small" onClick={() => beginSetup({ ticket })}>
              {t("common.retry")}
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
          <Typography.Text code copyable>
            {begin.data.secret}
          </Typography.Text>
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
      // 开启两步验证时只回挑战票(正式 token 由 setup/confirm 或 login/mfa 签发),关闭时直接拿到 token
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
      {/* en 用户登录页不该只能跟随浏览器语言;深底页用 dark 变体 */}
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
            {/* 挑战票不可跨账号复用:换号/放弃 MFA 必须清挑战回登录表单 */}
            <Button type="link" block onClick={() => setChallenge(null)}>
              {t("login.backToLogin")}
            </Button>
          </>
        ) : (
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
        )}
      </Card>
    </div>
  );
}
