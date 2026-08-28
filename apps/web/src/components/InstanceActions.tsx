/**
 * 实例操作组:开机/关机/更多(重启·事件·续费·自动续费·预留项·释放)。
 * 条目永不隐藏,灰置用 Tooltip 说明前置条件;预留项可见但禁用并注「即将上线」(ui-ux-spec 规则 2)。
 * 计费方式相关的几项按对象形态出,不出即「对这台实例不存在」:按量实例(running / stopped)出
 * 「转包周期」,包周期实例出「续费」与「自动续费」,竞价实例出「转按量」。
 * 转包周期是支付动作(后端先结清转换前那段按量账再翻 market,一次性预扣整段周期),
 * 确认在 RenewModal 里做,菜单点开即弹。
 * 释放走多级防护:键入实例名 + 勾选盘数据清除确认,两道都满足才解锁红按钮(ui-ux-spec 规则 4)。
 */

import { DownOutlined } from "@ant-design/icons";
import type { InstanceOut } from "@superdl/api-client";
import { isSubscriptionExpired } from "@superdl/ui";

import { App, Button, Checkbox, Dropdown, Input, Modal, Space, Tooltip, Typography } from "antd";
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

// creating 也可释放:调度长期不满足(如资源不足)时用户可主动取消
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
  // 破坏确认两道闸:键入实例名 + 勾选盘数据清除知情;creating 尚未落盘,只过键入这道
  const [typed, setTyped] = useState("");
  const [acked, setAcked] = useState(false);
  const { message } = App.useApp();
  const creating = instance.status === "creating";
  const nameMatched = typed.trim() === instance.name;
  const unlocked = nameMatched && (creating || acked);
  const release = useReleaseInstance({
    onSuccess: () => {
      message.success(creating ? t("instances.actions.createCanceled") : t("instances.actions.releaseStarted"));
      setTyped("");
      setAcked(false);
      onClose();
      onReleased?.();
    },
  });
  return (
    <Modal
      title={creating ? t("instances.actions.cancelModalTitle") : t("instances.actions.releaseModalTitle")}
      open={open}
      onCancel={() => {
        setTyped("");
        setAcked(false);
        onClose();
      }}
      footer={
        <Space>
          <Button onClick={onClose}>{t("instances.actions.cancel")}</Button>
          <Button
            danger
            type="primary"
            disabled={!unlocked}
            loading={release.isPending}
            onClick={() => release.mutate(instance.uuid)}
          >
            {creating ? t("instances.actions.confirmCancel") : t("instances.actions.confirmRelease")}
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph>
        {creating ? (
          <Trans
            i18nKey="instances.actions.cancelBody"
            values={{ name: instance.name, id: instance.uuid.slice(0, 8) }}
            components={{ b: <Typography.Text strong /> }}
          />
        ) : (
          <Trans
            i18nKey="instances.actions.releaseBody"
            values={{ name: instance.name, id: instance.uuid.slice(0, 8) }}
            components={{ b: <Typography.Text strong /> }}
          />
        )}
      </Typography.Paragraph>
      <Space orientation="vertical" size={8} style={{ width: "100%" }}>
        <Typography.Text type="secondary">
          {t("instances.actions.typeNameToConfirm", { name: instance.name })}
        </Typography.Text>
        <Input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={instance.name}
          maxLength={64}
          autoComplete="off"
          aria-label={t("instances.actions.typeNameToConfirm", { name: instance.name })}
        />
        {creating ? null : (
          <Checkbox checked={acked} onChange={(e) => setAcked(e.target.checked)}>
            {t("instances.actions.ackDiskWipe")}
          </Checkbox>
        )}
      </Space>
    </Modal>
  );
}

/** 灰置项 label 统一包 antd Tooltip。 */
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
  // 「包周期已到期,请先续费再开机」的事实源在后端 messages.py,前端不另写一份
  const { t: tErr } = useTranslation("errors");
  const { modal, message } = App.useApp();
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
  // 到期后开机后端直接 409(subscriptions.assert_active),按钮先灰掉并说明原因
  const expired = isSubscriptionExpired(instance.market, sub);

  // 转包周期只对按量实例出;状态不合适时灰置带原因(与后端 subscribe_instance 同款判据)
  const canConvert = instance.market === "on_demand";
  const convertBlocked = canConvert && s !== "running" && s !== "stopped";
  // 转按量与转包周期同款状态判据(后端 convert_to_on_demand 也只收 running / stopped)
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
    modal.confirm({
      title: t("instances.actions.stopConfirmTitle"),
      content: t("copy.stopConfirm"),
      okText: t("instances.actions.stopOk"),
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
            // 计费方式项按形态分化:按量出「转包周期」,包周期出「续费 / 自动续费」
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
            // 转按量:竞价档的退出口,点开先把「当前整点小时整体改按按量价」讲清楚
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
            // 在途状态与 frozen 后端一律拒:前者会和收敛路径抢同一行,后者那笔欠费得先还清
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
              modal.confirm({
                title: t("instances.actions.restartConfirmTitle"),
                content: t("instances.actions.restartConfirmBody"),
                onOk: () => restart.mutateAsync(instance.uuid),
              });
            } else if (key === "events") {
              onShowEvents?.();
            } else if (key === "renew") {
              setRenewOpen(true);
            } else if (key === "to-period") {
              setConvertOpen(true);
            } else if (key === "to-on-demand") {
              modal.confirm({
                title: t("spot.toOnDemandTitle"),
                content: (
                  <Space orientation="vertical" size={4}>
                    <span>{t("copy.spotToOnDemandRepriceHour")}</span>
                    <span>{t("copy.spotToOnDemandNoReclaim")}</span>
                    <span>{t("copy.spotToOnDemandNoRestart")}</span>
                  </Space>
                ),
                okText: t("spot.toOnDemandConfirm"),
                onOk: () => toOnDemand.mutateAsync(),
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
      {/* 按需挂载:关掉即卸载,重开就是一张新单(幂等键随之换新) */}
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
