/** 实例操作组:按状态与计费方式提供连接、启停、续费、转换及释放确认。 */

import type { InstanceOut } from "@superdl/api-client";
import { isSubscriptionExpired, space } from "@superdl/ui";
import { GatedButton, RowActions, TypeConfirmModal, useConfirm, type RowMenuItem } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { App, Button, Space, Typography } from "antd";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import {
  useConvertToOnDemand,
  useReleaseInstance,
  useRestartInstance,
  useSetAutoRenew,
  useStartInstance,
  useStopInstance,
} from "../api/mutations";
import { ConnectMenu } from "./ConnectMenu";
import { RenewModal } from "./RenewModal";

export function canReleaseStatus(s: string): boolean {
  return s === "stopped" || s === "frozen" || s === "failed" || s === "creating";
}

export function ReleaseModal({
  instance,
  open,
  onClose,
  onReleased,
}: {
  instance: InstanceOut;
  open: boolean;
  onClose: () => void;
  onReleased?: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const creating = instance.status === "creating";
  const [mountedAt] = useState(() => Date.now());
  const subExpiresAt = instance.subscription?.expires_at;
  const subDaysLeft =
    instance.market === "subscription" && subExpiresAt
      ? Math.max(0, Math.ceil((new Date(subExpiresAt).getTime() - mountedAt) / 86_400_000))
      : null;
  const release = useReleaseInstance({
    onSuccess: () => {
      message.success(creating ? t("instances.actions.createCanceled") : t("instances.actions.releaseStarted"));
      onClose();
      onReleased?.();
    },
  });
  return (
    <TypeConfirmModal
      open={open}
      title={creating ? t("instances.actions.cancelModalTitle") : t("instances.actions.releaseModalTitle")}
      body={
        creating ? (
          <Trans
            i18nKey="instances.actions.cancelBody"
            values={{ name: instance.name, id: instance.uuid.slice(0, 8) }}
            components={{ b: <Typography.Text strong /> }}
          />
        ) : (
          <Space orientation="vertical" size={space.sm}>
            <Trans
              i18nKey="instances.actions.releaseBody"
              values={{ name: instance.name, id: instance.uuid.slice(0, 8) }}
              components={{ b: <Typography.Text strong /> }}
            />
            {subDaysLeft != null && (
              <Typography.Text type="danger">
                {t("instances.actions.releaseSubscriptionNote", { days: subDaysLeft })}
              </Typography.Text>
            )}
          </Space>
        )
      }
      targetName={instance.name}
      checkboxLabel={creating ? undefined : t("instances.actions.ackDiskWipe")}
      confirmLabel={creating ? t("instances.actions.confirmCancel") : t("instances.actions.confirmRelease")}
      cancelLabel={t("instances.actions.cancel")}
      loading={release.isPending}
      onConfirm={() => release.mutate(instance.uuid)}
      onCancel={onClose}
    />
  );
}

export function InstanceActions({
  instance,
  onShowEvents,
  size = "small",
}: {
  instance: InstanceOut;
  onShowEvents?: () => void;
  /** 行内 small / 详情页头 middle */
  size?: "small" | "middle";
}) {
  const { t } = useTranslation();
  const { t: tErr } = useTranslation("errors");
  const { message } = App.useApp();
  const confirm = useConfirm();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [renewOpen, setRenewOpen] = useState(false);
  const [convertOpen, setConvertOpen] = useState(false);
  const start = useStartInstance();
  const stop = useStopInstance();
  const restart = useRestartInstance();
  const autoRenew = useSetAutoRenew(instance.uuid, {
    onSuccess: (data) => {
      message.success(data.subscription?.auto_renew ? t("period.autoRenewOn") : t("period.autoRenewOff"));
    },
  });
  const toOnDemand = useConvertToOnDemand(instance.uuid, {
    onSuccess: () => {
      message.success(t("spot.toOnDemandOk"), 6);
    },
  });
  const s = instance.status;
  const sub = instance.subscription;
  const isSubscription = instance.market === "subscription";
  const isSpot = instance.market === "spot";
  const expired = isSubscriptionExpired(instance.market, sub);

  const canConvert = instance.market === "on_demand";
  const convertBlocked = canConvert && s !== "running" && s !== "stopped";
  const toOnDemandBlocked = isSpot && s !== "running" && s !== "stopped";

  const canStart = s === "stopped" && !expired;
  const canStop = s === "running";
  const canRestart = s === "running";
  const canRelease = canReleaseStatus(s);

  const startTip = expired
    ? tErr("billing.subscriptionExpired")
    : s === "frozen"
      ? t("copy.frozenNeedsRecharge")
      : t("copy.startNeedsStopped");

  const confirmStop = () =>
    confirm({
      title: t("instances.actions.stopConfirmTitle"),
      consequences: [t(isSubscription ? "copy.stopConfirmSubscription" : "copy.stopConfirm")],
      okText: t("instances.actions.stopOk"),
      danger: true,
      onOk: async () => {
        await stop.mutateAsync(instance.uuid);
        message.success(t("instances.actions.stopped"));
      },
    });

  const primary =
    s === "running" ? (
      <ConnectMenu instance={instance} size={size} />
    ) : s === "failed" ? (
      <Link to="/market/create/$skuId" params={{ skuId: String(instance.sku_id) }}>
        <Button type="primary" size={size}>
          {t("instances.recreate")}
        </Button>
      </Link>
    ) : (
      <GatedButton
        type="primary"
        size={size}
        reason={canStart ? undefined : startTip}
        loading={start.isPending}
        onClick={() => start.mutate(instance.uuid)}
      >
        {t("instances.actions.start")}
      </GatedButton>
    );

  const secondary = canStop ? (
    <Button size={size} loading={stop.isPending} onClick={confirmStop}>
      {t("instances.actions.stop")}
    </Button>
  ) : (
    <Button size={size} onClick={onShowEvents}>
      {t("instances.actions.eventsLog")}
    </Button>
  );

  const more: RowMenuItem[] = [
    {
      key: "restart",
      label: t("instances.actions.restart"),
      reason: canRestart ? undefined : t("copy.stopNeedsRunning"),
      onClick: () =>
        confirm({
          title: t("instances.actions.restartConfirmTitle"),
          consequences: [t("instances.actions.restartConfirmBody")],
          danger: true,
          onOk: async () => {
            await restart.mutateAsync(instance.uuid);
          },
        }),
    },
    ...(canStop ? [{ key: "events", label: t("instances.actions.eventsLog"), onClick: () => onShowEvents?.() }] : []),
    { type: "divider", key: "d-ops" },
    ...(isSubscription
      ? [
          { key: "renew", label: t("period.renewMenu"), onClick: () => setRenewOpen(true) },
          {
            key: "auto-renew",
            label: sub?.auto_renew ? t("period.autoRenewOffMenu") : t("period.autoRenewOnMenu"),
            onClick: () => autoRenew.mutate(!sub?.auto_renew),
          },
          { type: "divider" as const, key: "d-sub" },
        ]
      : []),
    ...(isSpot
      ? [
          {
            key: "to-on-demand",
            label: t("spot.toOnDemandMenu"),
            reason: toOnDemandBlocked ? tErr("orchestrator.convertNeedsRunningOrStopped") : undefined,
            onClick: () =>
              confirm({
                title: t("spot.toOnDemandTitle"),
                consequences: [
                  t("copy.spotToOnDemandRepriceHour"),
                  t("copy.spotToOnDemandNoReclaim"),
                  t("copy.spotToOnDemandNoRestart"),
                ],
                okText: t("spot.toOnDemandConfirm"),
                onOk: async () => {
                  await toOnDemand.mutateAsync();
                },
              }),
          },
          { type: "divider" as const, key: "d-spot" },
        ]
      : []),
    ...(canConvert
      ? [
          {
            key: "to-period",
            label: t("instances.actions.toPeriod"),
            reason: convertBlocked ? tErr("orchestrator.convertNeedsRunningOrStopped") : undefined,
            onClick: () => setConvertOpen(true),
          },
          { type: "divider" as const, key: "d-period" },
        ]
      : []),
    {
      key: "release",
      danger: true,
      label: s === "creating" ? t("instances.actions.cancelCreate") : t("instances.actions.releaseMenu"),
      reason: canRelease ? undefined : t("copy.releaseNeedsStopped"),
      onClick: () => setReleaseOpen(true),
    },
  ];

  return (
    <>
      <RowActions primary={primary} secondary={secondary} more={more} size={size} />
      <ReleaseModal instance={instance} open={releaseOpen} onClose={() => setReleaseOpen(false)} />
      {isSubscription && renewOpen && <RenewModal instance={instance} open onClose={() => setRenewOpen(false)} />}
      {canConvert && convertOpen && (
        <RenewModal instance={instance} mode="subscribe" open onClose={() => setConvertOpen(false)} />
      )}
    </>
  );
}
