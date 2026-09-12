/** 服务操作组:停止 / 启动 互斥常显,更多 ▾ 放访问密钥 / 设置直达与删除;条目永不隐藏,灰置用 Tooltip。停止二次确认;删除走键入名称 + 勾选的多级防护,运行中须先停(后端 409 同判据)。 */

import { DownOutlined } from "@ant-design/icons";
import type { ServiceOut } from "@superdl/api-client";
import { TypeConfirmModal, useConfirm } from "@superdl/ui/components";
import { useNavigate } from "@tanstack/react-router";
import { App, Button, Dropdown, Space, Tooltip, Typography } from "antd";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { useDeleteService, useStartService, useStopService } from "../../api/mutations";

export function canStopService(status: string): boolean {
  return status === "running" || status === "unready";
}

export function canStartService(status: string): boolean {
  return status === "stopped" || status === "failed";
}

export function DeleteServiceModal({
  service,
  open,
  onClose,
  onDeleted,
}: {
  service: ServiceOut;
  open: boolean;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const remove = useDeleteService({
    onSuccess: () => {
      message.success(t("services.actions.deleteStarted"));
      onClose();
      onDeleted?.();
    },
  });
  return (
    <TypeConfirmModal
      open={open}
      title={t("services.actions.deleteModalTitle")}
      body={
        <Trans
          i18nKey="services.actions.deleteBody"
          values={{ name: service.name, slug: service.slug }}
          components={{ b: <Typography.Text strong /> }}
        />
      }
      targetName={service.name}
      checkboxLabel={t("services.actions.ackEndpointLost")}
      confirmLabel={t("services.actions.confirmDelete")}
      cancelLabel={t("services.actions.cancel")}
      loading={remove.isPending}
      onConfirm={() => remove.mutate(service.slug)}
      onCancel={onClose}
    />
  );
}

function tipped(label: string, tip?: string) {
  return tip ? <Tooltip title={tip}>{label}</Tooltip> : label;
}

export function canRolloutService(service: ServiceOut): { ok: boolean; reason?: "subscription" | "unsettled" } {
  const s = service.status;
  if (service.current_instance?.market === "subscription") return { ok: false, reason: "subscription" };
  if (s === "running" || s === "unready" || s === "stopped" || s === "failed") return { ok: true };
  return { ok: false, reason: "unsettled" };
}

export function ServiceActions({
  service,
  onDeleted,
  onRollout,
}: {
  service: ServiceOut;
  onDeleted?: () => void;
  /** 详情页头部给:出「更新版本」按钮 */
  onRollout?: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const goTab = (tab: "keys" | "settings") =>
    void navigate({ to: "/services/$slug", params: { slug: service.slug }, search: { tab } });
  const stop = useStopService({ onSuccess: () => message.success(t("services.actions.stopped")) });
  const start = useStartService({ onSuccess: () => message.success(t("services.actions.started")) });
  const s = service.status;
  const stoppable = canStopService(s);
  const startable = canStartService(s);
  const deletable = s === "stopped" || s === "frozen" || s === "failed" || s === "deploying";
  const isSubscription = service.current_instance?.market === "subscription";
  const rollout = canRolloutService(service);

  return (
    <Space>
      {startable ? (
        <Button size="small" type="primary" loading={start.isPending} onClick={() => start.mutate(service.slug)}>
          {t("services.actions.start")}
        </Button>
      ) : (
        <Tooltip title={stoppable ? undefined : t("services.actions.needsRunning")}>
          <Button
            size="small"
            disabled={!stoppable}
            loading={stop.isPending}
            onClick={() =>
              confirm({
                title: t("services.actions.stopConfirmTitle", { name: service.name }),
                consequences: [
                  t("services.actions.stopConfirmBody"),
                  ...(isSubscription ? [t("services.actions.stopConfirmSubscription")] : []),
                ],
                onOk: async () => {
                  await stop.mutateAsync(service.slug);
                },
              })
            }
          >
            {t("services.actions.stop")}
          </Button>
        </Tooltip>
      )}
      {onRollout && (
        <Tooltip
          title={
            rollout.ok
              ? undefined
              : rollout.reason === "subscription"
                ? t("services.revision.subscriptionUnsupported")
                : t("services.revision.needsSettled")
          }
        >
          <Button size="small" disabled={!rollout.ok} onClick={onRollout}>
            {t("services.actions.rollout")}
          </Button>
        </Tooltip>
      )}
      <Dropdown
        menu={{
          items: [
            { key: "keys", label: t("services.actions.keys"), onClick: () => goTab("keys") },
            { key: "settings", label: t("services.actions.settings"), onClick: () => goTab("settings") },
            { type: "divider" },
            {
              key: "delete",
              danger: true,
              disabled: !deletable,
              label: tipped(
                t("services.actions.delete"),
                deletable ? undefined : t("services.actions.deleteNeedsStopped"),
              ),
              onClick: () => setDeleteOpen(true),
            },
          ],
        }}
      >
        <Button size="small">
          {t("services.actions.more")} <DownOutlined />
        </Button>
      </Dropdown>
      <DeleteServiceModal
        service={service}
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onDeleted={onDeleted}
      />
    </Space>
  );
}
