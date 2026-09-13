/** 服务设置 Tab:访问鉴权开关 / 调试 SSH / 危险区(改名在头部 EntityHeader 完成)。鉴权只 PATCH services 行,不重新部署;关鉴权走 L2 确认;开着鉴权却没有可用 Key 时常驻提醒;SSH 随版本固定,只回显。 */

import type { ServiceOut } from "@superdl/api-client";
import { CopyField, GatedButton, useConfirm } from "@superdl/ui/components";
import { Alert, App, Button, Card, Space, Switch, theme, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useUpdateService } from "../../api/mutations";
import { useInstanceAccess, useServiceApiKeys } from "../../api/queries";
import { DeleteServiceModal } from "./ServiceActions";
import { space } from "@superdl/ui";

export function SettingsTab({
  service,
  onGoKeys,
  onDeleted,
}: {
  service: ServiceOut;
  onGoKeys: () => void;
  onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const confirm = useConfirm();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const update = useUpdateService(service.slug);
  const keysQ = useServiceApiKeys(service.slug);
  const liveKeys = (keysQ.data ?? []).filter((k) => k.revoked_at == null).length;
  const released = service.released_at != null;
  const live = service.status === "running" || service.status === "unready";
  const inst = service.current_instance;
  const withSsh = service.container?.with_ssh ?? false;
  const accessQ = useInstanceAccess(inst?.uuid ?? "", { enabled: live && withSsh && inst != null });
  const deletable =
    service.status === "stopped" ||
    service.status === "frozen" ||
    service.status === "failed" ||
    service.status === "deploying";

  const setAuth = async (on: boolean) => {
    await update.mutateAsync({ require_api_key: on });
    message.success(t("services.settings.authSaved"));
  };
  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Card size="small" title={t("services.settings.authCard")}>
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
          <Space>
            <Switch
              checked={service.require_api_key}
              disabled={released}
              loading={update.isPending}
              aria-label={t("services.settings.authCard")}
              onChange={(on) => {
                if (on) {
                  void setAuth(true);
                  return;
                }
                confirm({
                  title: t("services.settings.authOffConfirmTitle"),
                  consequences: [t("services.settings.authOffConfirmBody"), t("services.settings.authOffConfirmKeep")],
                  danger: true,
                  onOk: () => setAuth(false),
                });
              }}
            />
            <Typography.Text>
              {service.require_api_key ? t("services.authRequired") : t("services.authPublic")}
            </Typography.Text>
          </Space>
          <Typography.Text type="secondary">{t("services.settings.authEffectNote")}</Typography.Text>
          <Typography.Text type="secondary">{t("copy.serviceGatewayAuth")}</Typography.Text>
          {service.require_api_key && keysQ.isSuccess && liveKeys === 0 && !released && (
            <Alert
              type="warning"
              showIcon
              title={t("services.settings.noLiveKeyWarn")}
              action={
                <Button size="small" onClick={onGoKeys}>
                  {t("services.settings.goKeys")}
                </Button>
              }
            />
          )}
        </Space>
      </Card>

      <Card size="small" title={t("services.settings.sshCard")}>
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
          {!withSsh ? (
            <Typography.Text type="secondary">{t("services.settings.sshFixedNote")}</Typography.Text>
          ) : !live ? (
            <Alert type="info" showIcon title={t("services.settings.sshNotRunning")} />
          ) : (
            <>
              {accessQ.data?.ssh_command ? (
                <CopyField value={accessQ.data.ssh_command} code label={t("instances.copyCommand")} />
              ) : (
                <Typography.Text code>…</Typography.Text>
              )}
              <Typography.Text type="secondary">{t("copy.sshKeyOnly")}</Typography.Text>
            </>
          )}
          {withSsh && <Typography.Text type="secondary">{t("services.settings.sshOpenNote")}</Typography.Text>}
        </Space>
      </Card>

      <Card size="small" title={t("services.detail.dangerZone")} style={{ borderColor: token.colorErrorBorder }}>
        <Space orientation="vertical">
          <Typography.Text type="secondary">{t("services.detail.dangerNote")}</Typography.Text>
          <GatedButton
            danger
            reason={deletable ? undefined : t("services.actions.deleteNeedsStopped")}
            onClick={() => setDeleteOpen(true)}
          >
            {t("services.actions.delete")}
          </GatedButton>
        </Space>
      </Card>
      <DeleteServiceModal
        service={service}
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onDeleted={onDeleted}
      />
    </Space>
  );
}
