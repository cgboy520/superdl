/** 服务操作组:状态门控的启停与端点菜单、版本更新、设置入口和删除确认。 */

import { LinkOutlined } from "@ant-design/icons";
import type { ServiceOut } from "@superdl/api-client";
import { fontSize, space } from "@superdl/ui";
import {
  CopyButton,
  GatedButton,
  RowActions,
  RowMoreMenu,
  TypeConfirmModal,
  useConfirm,
  type RowMenuItem,
} from "@superdl/ui/components";
import { useNavigate } from "@tanstack/react-router";
import { App, Button, Modal, Space, Typography } from "antd";
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

/** 调用示例 curl:与服务详情概览同一构造;行内拿不到 Key 列表,只给前缀占位。 */
function curlExampleOf(service: ServiceOut): string {
  return [
    `curl ${service.url}`,
    ...(service.require_api_key ? ['  -H "Authorization: Bearer sk-xxxxxxxx…"'] : []),
  ].join(" \\\n");
}

/** 端点 ▾(对应实例的「连接 ▾」):复制访问地址 / 打开端点 / 调用示例;服务未运行时条目灰置带原因。 */
function EndpointMenu({ service, size }: { service: ServiceOut; size: "small" | "middle" }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [curlOpen, setCurlOpen] = useState(false);
  const reason =
    service.status === "running" || service.status === "unready" ? undefined : t("services.endpointUnreachable");
  const curl = curlExampleOf(service);
  const items: RowMenuItem[] = [
    {
      key: "copy",
      label: t("services.actions.copyUrl"),
      reason,
      onClick: () => {
        void navigator.clipboard.writeText(service.url).then(() => message.success(t("common.copied")));
      },
    },
    {
      key: "open",
      label: t("services.actions.openEndpoint"),
      reason,
      onClick: () => {
        window.open(service.url, "_blank", "noopener,noreferrer");
      },
    },
    { key: "curl", label: t("services.actions.curlExample"), reason, onClick: () => setCurlOpen(true) },
  ];
  return (
    <>
      <RowMoreMenu
        items={items}
        size={size}
        label={t("services.actions.endpointMenu")}
        type="primary"
        icon={<LinkOutlined />}
      />
      <Modal
        title={t("services.actions.curlExample")}
        open={curlOpen}
        onCancel={() => setCurlOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
          <pre
            style={{
              margin: 0,
              fontSize: fontSize.caption,
              lineHeight: 1.8,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {curl}
          </pre>
          <CopyButton text={curl} label={t("instances.copyCommand")} />
          {service.require_api_key && (
            <Typography.Text type="secondary">{t("services.detail.curlKeyPlaceholderNote")}</Typography.Text>
          )}
        </Space>
      </Modal>
    </>
  );
}

export function ServiceActions({
  service,
  onDeleted,
  onRollout,
  size = "small",
}: {
  service: ServiceOut;
  onDeleted?: () => void;
  /** 详情页头部给:出「更新版本」按钮 */
  onRollout?: () => void;
  /** 行内 small / 详情页头 middle */
  size?: "small" | "middle";
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

  const primary = startable ? (
    <Button size={size} type="primary" loading={start.isPending} onClick={() => start.mutate(service.slug)}>
      {t("services.actions.start")}
    </Button>
  ) : (
    <EndpointMenu service={service} size={size} />
  );

  const stopOrStart = stoppable ? (
    <Button size={size} loading={stop.isPending} onClick={confirmStop}>
      {t("services.actions.stop")}
    </Button>
  ) : startable ? undefined : (
    <GatedButton
      size={size}
      reason={s === "frozen" ? t("copy.frozenNeedsRecharge") : t("services.actions.needsStopped")}
    >
      {t("services.actions.start")}
    </GatedButton>
  );
  const rolloutButton = onRollout ? (
    <GatedButton
      size={size}
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
  const secondary =
    stopOrStart && rolloutButton ? (
      <Space size={space.xs}>
        {stopOrStart}
        {rolloutButton}
      </Space>
    ) : (
      (rolloutButton ?? stopOrStart)
    );

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
      <RowActions primary={primary} secondary={secondary} more={more} size={size} />
      <DeleteServiceModal
        service={service}
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onDeleted={onDeleted}
      />
    </>
  );
}
