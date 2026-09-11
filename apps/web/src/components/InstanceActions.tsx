/** 实例操作组:开机/关机/更多(重启·事件·续费·自动续费·预留项·释放)。条目永不隐藏,灰置用 Tooltip 说明前置条件;预留项禁用并注「即将上线」(ui-ux-spec 规则 2)。按形态出计费项:按量出「转包周期」,包周期出「续费 / 自动续费」,竞价出「转按量」;转包周期确认在 RenewModal 里做。释放走多级防护:键入实例名 + 勾选盘数据清除(ui-ux-spec 规则 4)。 */

import { DownOutlined } from "@ant-design/icons";
import type { InstanceOut } from "@superdl/api-client";
import { isSubscriptionExpired } from "@superdl/ui";
import { TypeConfirmModal, useConfirm } from "@superdl/ui/components";

import { App, Button, Dropdown, Space, Tooltip, Typography } from "antd";
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
import { RenewModal } from "./RenewModal";

// creating 也可释放
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
  // creating 尚未落盘,只过键入这道闸,不出清盘勾选
  const creating = instance.status === "creating";
  // 包周期释放额外写明「预付不退款、剩余天数作废」(ui-ux-spec §3.5);「现在」在挂载时定一次
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
          <Space orientation="vertical" size={8}>
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

function tipped(label: string, tip?: string) {
  return tip ? <Tooltip title={tip}>{label}</Tooltip> : label;
}

export function InstanceActions({
  instance,
  onShowEvents,
}: {
  instance: InstanceOut;
  onShowEvents?: () => void;
}) {
  const { t } = useTranslation();
  // 「包周期已到期,请先续费再开机」文案事实源在后端 messages.py
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
    onSuccess: (data) =>
      message.success(
        data.subscription?.auto_renew ? t("period.autoRenewOn") : t("period.autoRenewOff"),
      ),
  });
  const toOnDemand = useConvertToOnDemand(instance.uuid, {
    onSuccess: () => message.success(t("spot.toOnDemandOk"), 6),
  });
  const s = instance.status;
  const sub = instance.subscription;
  const isSubscription = instance.market === "subscription";
  const isSpot = instance.market === "spot";
  const expired = isSubscriptionExpired(instance.market, sub);

  // 转包周期只对按量实例出;状态不合适时灰置带原因(与后端 subscribe_instance 同判据)
  const canConvert = instance.market === "on_demand";
  const convertBlocked = canConvert && s !== "running" && s !== "stopped";
  // 转按量同款状态判据(后端 convert_to_on_demand 只收 running / stopped)
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
      // 按量强调「再开机可能没库存」;包周期关机不退费但保留库存(ui-ux-spec §3.5)
      consequences: [t(isSubscription ? "copy.stopConfirmSubscription" : "copy.stopConfirm")],
      okText: t("instances.actions.stopOk"),
      danger: true,
      onOk: async () => {
        await stop.mutateAsync(instance.uuid);
        message.success(t("instances.actions.stopped"));
      },
    });

  return (
    <Space size={4}>
      <Tooltip title={canStart ? undefined : startTip}>
        <Button
          size="small"
          disabled={!canStart}
          loading={start.isPending}
          onClick={() => start.mutate(instance.uuid)}
        >
          {t("instances.actions.start")}
        </Button>
      </Tooltip>
      <Tooltip title={canStop ? undefined : t("copy.stopNeedsRunning")}>
        <Button size="small" disabled={!canStop} loading={stop.isPending} onClick={confirmStop}>
          {t("instances.actions.stop")}
        </Button>
      </Tooltip>
      <Dropdown
        menu={{
          items: [
            {
              key: "restart",
              label: tipped(t("instances.actions.restart"), canRestart ? undefined : t("copy.stopNeedsRunning")),
              disabled: !canRestart,
            },
            { key: "events", label: t("instances.actions.eventsLog") },
            { type: "divider" },
            // 按形态分化:按量出「转包周期」,包周期出「续费 / 自动续费」
            ...(isSubscription
              ? [
                  { key: "renew", label: t("period.renewMenu") },
                  {
                    key: "auto-renew",
                    label: sub?.auto_renew
                      ? t("period.autoRenewOffMenu")
                      : t("period.autoRenewOnMenu"),
                    disabled: sub == null,
                  },
                  { type: "divider" as const },
                ]
              : []),
                  ...(isSpot
              ? [
                  {
                    key: "to-on-demand",
                    label: tipped(
                      t("spot.toOnDemandMenu"),
                      toOnDemandBlocked
                        ? tErr("orchestrator.convertNeedsRunningOrStopped")
                        : undefined,
                    ),
                    disabled: toOnDemandBlocked,
                  },
                  { type: "divider" as const },
                ]
              : []),
            ...(canConvert
              ? [
                  {
                    key: "to-period",
                    label: tipped(
                      t("instances.actions.toPeriod"),
                      convertBlocked ? tErr("orchestrator.convertNeedsRunningOrStopped") : undefined,
                    ),
                    disabled: convertBlocked,
                  },
                  { type: "divider" as const },
                ]
              : []),
            {
              key: "cardless",
              label: tipped(t("instances.actions.cardless"), t("copy.comingSoon")),
              disabled: true,
            },
            { type: "divider" },
            {
              key: "release",
              danger: true,
              label: tipped(
                s === "creating" ? t("instances.actions.cancelCreate") : t("instances.actions.releaseMenu"),
                canRelease ? undefined : t("copy.releaseNeedsStopped"),
              ),
              disabled: !canRelease,
            },
          ],
          onClick: ({ key }) => {
            if (key === "restart") {
              confirm({
                title: t("instances.actions.restartConfirmTitle"),
                consequences: [t("instances.actions.restartConfirmBody")],
                danger: true,
                onOk: async () => {
                  await restart.mutateAsync(instance.uuid);
                },
              });
            } else if (key === "events") {
              onShowEvents?.();
            } else if (key === "renew") {
              setRenewOpen(true);
            } else if (key === "to-period") {
              setConvertOpen(true);
            } else if (key === "to-on-demand") {
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
              });
            } else if (key === "auto-renew") {
              autoRenew.mutate(!sub?.auto_renew);
            } else if (key === "release") {
              setReleaseOpen(true);
            }
          },
        }}
      >
        <Button size="small">
          {t("instances.actions.more")} <DownOutlined />
        </Button>
      </Dropdown>
      <ReleaseModal instance={instance} open={releaseOpen} onClose={() => setReleaseOpen(false)} />
      {isSubscription && renewOpen && (
        <RenewModal instance={instance} open onClose={() => setRenewOpen(false)} />
      )}
      {canConvert && convertOpen && (
        <RenewModal
          instance={instance}
          mode="subscribe"
          open
          onClose={() => setConvertOpen(false)}
        />
      )}
    </Space>
  );
}
