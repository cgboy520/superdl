/** 平台配置连通性测试卡:短信 / 镜像仓库。 */

import { App, Button, Card, Input, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";

import { useTestRegistry, useTestSms } from "../../api";
import { PROVIDER_LABELS } from "./-platformFields";

export function SmsTestCard({ disabled }: { disabled: boolean }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const [phone, setPhone] = useState("");
  const testSms = useTestSms({
    mutation: {
      onSuccess: (d) => {
        message.success(t("platform.testSmsSent", { provider: PROVIDER_LABELS[d.provider] ?? d.provider }));
      },
      onError: (e) => message.error(errText(e, t("platform.sendFailed"))),
    },
  });
  return (
    <Card size="small" title={t("platform.testSmsTitle")}>
      <Space.Compact style={{ width: 360 }}>
        <Input
          placeholder={t("platform.testSmsPhone")}
          value={phone}
          maxLength={11}
          disabled={disabled}
          onChange={(e) => setPhone(e.target.value)}
        />
        <Button
          type="primary"
          disabled={disabled || !/^1\d{10}$/.test(phone)}
          loading={testSms.isPending}
          onClick={() => testSms.mutate({ data: { phone } })}
        >
          {t("platform.testSmsSend")}
        </Button>
      </Space.Compact>
      <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption, marginTop: 8 }}>
        {t("platform.testSmsNote")}
      </div>
    </Card>
  );
}

export function RegistryTestCard({ disabled }: { disabled: boolean }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const test = useTestRegistry({
    mutation: { onError: (e) => message.error(errText(e, t("platform.sendFailed"))) },
  });
  const r = test.data;
  return (
    <Card size="small" title={t("platform.testRegistryTitle")}>
      <Space orientation="vertical" size={8}>
        <Button type="primary" disabled={disabled} loading={test.isPending} onClick={() => test.mutate()}>
          {t("platform.testRegistryRun")}
        </Button>
        {r && (
          <Typography.Text style={{ color: r.ok ? adminColors.positive : adminColors.negative }}>
            {r.ok
              ? t("platform.registryOk", {
                  version: r.harbor_version ? `(Harbor ${r.harbor_version})` : "",
                  repos: r.repositories ?? "?",
                })
              : t("platform.registryFailed", { step: r.step, detail: r.detail })}
          </Typography.Text>
        )}
        <div style={{ color: adminColors.textSecondary, fontSize: fontSize.caption }}>{t("platform.testRegistryNote")}</div>
      </Space>
    </Card>
  );
}

