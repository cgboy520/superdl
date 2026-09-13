/** 服务操作组(RowActions 三槽位):主动作随状态变(可启动 → 启动 / 可停止 → 停止 / 其余 → 启动灰置带原因)+ 次动作(详情页头的「更新版本」)+ 更多 ▾(访问密钥 / 设置直达与删除)。条目永不隐藏,灰置带原因。停止二次确认;删除走键入名称 + 勾选的多级防护,运行中须先停(后端 409 同判据)。 */

import type { ServiceOut } from "@superdl/api-client";
import { GatedButton, RowActions, TypeConfirmModal, useConfirm, type RowMenuItem } from "@superdl/ui/components";
import { useNavigate } from "@tanstack/react-router";
import { App, Button, Typography } from "antd";
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
  const stop = useStopService({
    onSuccess: () => {
      message.success(t("services.actions.stopped"));
    },
  });
  const start = useStartService({
    onSuccess: () => {
      message.success(t("services.actions.started"));
    },
  });
  const s = service.status;
  const stoppable = canStopService(s);
  const startable = canStartService(s);
  const deletable = s === "stopped" || s === "frozen" || s === "failed" || s === "deploying";
  const isSubscription = service.current_instance?.market === "subscription";
  const rollout = canRolloutService(service);

  const confirmStop = () =>
    confirm({
      title: t("services.actions.stopConfirmTitle", { name: service.name }),
      consequences: [
        t("services.actions.stopConfirmBody"),
        ...(isSubscription ? [t("services.actions.stopConfirmSubscription")] : []),
      ],
      onOk: async () => {
        await stop.mutateAsync(service.slug);
      },
    });

  // 主动作随状态变:可启动 → 启动;可停止 → 停止;过渡态 / 冻结 → 启动灰置带原因
  const primary = startable ? (
    <Button size="small" type="primary" loading={start.isPending} onClick={() => start.mutate(service.slug)}>
      {t("services.actions.start")}
    </Button>
  ) : stoppable ? (
    <Button size="small" loading={stop.isPending} onClick={confirmStop}>
      {t("services.actions.stop")}
    </Button>
  ) : (
    <GatedButton
      size="small"
      type="primary"
      reason={s === "frozen" ? t("copy.frozenNeedsRecharge") : t("services.actions.needsStopped")}
    >
      {t("services.actions.start")}
    </GatedButton>
  );

  const secondary = onRollout ? (
    <GatedButton
      size="small"
      reason={
        rollout.ok
          ? undefined
          : rollout.reason === "subscription"
            ? t("services.revision.subscriptionUnsupported")
            : t("services.revision.needsSettled")
      }
      onClick={onRollout}
    >
      {t("services.actions.rollout")}
    </GatedButton>
  ) : undefined;

  const more: RowMenuItem[] = [
    { key: "keys", label: t("services.actions.keys"), onClick: () => goTab("keys") },
    { key: "settings", label: t("services.actions.settings"), onClick: () => goTab("settings") },
    { type: "divider", key: "d-delete" },
    {
      key: "delete",
      danger: true,
      label: t("services.actions.delete"),
      reason: deletable ? undefined : t("services.actions.deleteNeedsStopped"),
      onClick: () => setDeleteOpen(true),
    },
  ];

  return (
    <>
      <RowActions primary={primary} secondary={secondary} more={more} />
      <DeleteServiceModal
        service={service}
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onDeleted={onDeleted}
      />
    </>
  );
}
