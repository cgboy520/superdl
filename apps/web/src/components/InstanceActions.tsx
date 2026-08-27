/**
 * 实例操作组:开机/关机/更多(重启·事件·预留项·释放)。
 * 条目永不隐藏,灰置用 Tooltip 说明前置条件;预留项(无卡模式/转包年包月)可见但禁用,
 * 注「即将上线」——只有已排期的能力才留占位,没排期的直接不进 UI(见 ui-ux-spec 规则 2);
 * 释放走多级防护(复述名称+ID、键入实例名、勾选盘数据清除确认
 * 两道都满足才解锁红按钮 —— 见 docs/ui-ux-spec.md 规则 4)。
 */

import { DownOutlined } from "@ant-design/icons";
import type { InstanceOut } from "@superdl/api-client";

import { App, Button, Checkbox, Dropdown, Input, Modal, Space, Tooltip, Typography } from "antd";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import {
  useReleaseInstance,
  useRestartInstance,
  useStartInstance,
  useStopInstance,
} from "../api/mutations";

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
  // 破坏确认两道闸:键入实例名 + 勾选盘数据清除知情。
  // creating 尚未落盘,取消创建只过键入这道,不弹无意义的清盘确认。
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
  const { modal, message } = App.useApp();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const start = useStartInstance();
  const stop = useStopInstance();
  const restart = useRestartInstance();
  const s = instance.status;

  const canStart = s === "stopped";
  const canStop = s === "running";
  const canRestart = s === "running";
  const canRelease = canReleaseStatus(s);

  const startTip = s === "frozen" ? t("copy.frozenNeedsRecharge") : t("copy.startNeedsStopped");

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
            {
              key: "cardless",
              label: tipped(t("instances.actions.cardless"), t("copy.comingSoon")),
              disabled: true,
            },
            {
              key: "to-period",
              label: tipped(t("instances.actions.toPeriod"), t("copy.comingSoon")),
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
    </Space>
  );
}
