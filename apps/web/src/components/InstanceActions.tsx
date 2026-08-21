/**
 * 实例操作组:开机/关机/更多(重启·事件·预留项·释放)。
 * 铁律 #2:条目永不隐藏,灰置用 antd Tooltip 说明前置条件;P1 预留项(无卡模式/保存镜像/
 * 转包年包月)可见但禁用,注「即将上线」。
 * 铁律 #4:释放多级防护(复述名称+ID、勾选确认才解锁红色按钮)。
 */

import { DownOutlined } from "@ant-design/icons";
import type { InstanceOut } from "@superdl/api-client";
import { copy } from "@superdl/ui";
import { App, Button, Checkbox, Dropdown, Modal, Space, Tooltip, Typography } from "antd";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import {
  useReleaseInstance,
  useRestartInstance,
  useStartInstance,
  useStopInstance,
} from "../api/mutations";

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
  const [checked, setChecked] = useState(false);
  const { message } = App.useApp();
  const creating = instance.status === "creating";
  const release = useReleaseInstance({
    onSuccess: () => {
      message.success(creating ? t("instances.actions.createCanceled") : t("instances.actions.releaseStarted"));
      onClose();
      onReleased?.();
    },
  });
  return (
    <Modal
      title={creating ? t("instances.actions.cancelModalTitle") : t("instances.actions.releaseModalTitle")}
      open={open}
      onCancel={() => {
        setChecked(false);
        onClose();
      }}
      footer={
        <Space>
          <Button onClick={onClose}>{t("instances.actions.cancel")}</Button>
          <Button
            danger
            type="primary"
            disabled={!checked}
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
      <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>
        {copy.releaseConfirmChecklist}
      </Checkbox>
    </Modal>
  );
}

/** 灰置项 label 统一包 antd Tooltip(替代原生 title,反馈即时且样式可控) */
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
  // creating 也可释放:调度长期不满足(如资源不足)时用户可主动取消,不必干等超时
  const canRelease =
    s === "stopped" || s === "frozen" || s === "failed" || s === "creating";

  const startTip = s === "frozen" ? copy.frozenNeedsRecharge : copy.startNeedsStopped;

  const confirmStop = () =>
    modal.confirm({
      title: t("instances.actions.stopConfirmTitle"),
      content: copy.stopConfirm,
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
      <Tooltip title={canStop ? undefined : copy.stopNeedsRunning}>
        <Button size="small" disabled={!canStop} onClick={confirmStop}>
          {t("instances.actions.stop")}
        </Button>
      </Tooltip>
      <Dropdown
        menu={{
          items: [
            {
              key: "restart",
              label: tipped(t("instances.actions.restart"), canRestart ? undefined : copy.stopNeedsRunning),
              disabled: !canRestart,
            },
            { key: "events", label: t("instances.actions.eventsLog") },
            { type: "divider" },
            {
              key: "cardless",
              label: tipped(t("instances.actions.cardless"), copy.comingSoon),
              disabled: true,
            },
            {
              key: "save-image",
              label: tipped(t("instances.actions.saveImage"), copy.comingSoon),
              disabled: true,
            },
            {
              key: "to-period",
              label: tipped(t("instances.actions.toPeriod"), copy.comingSoon),
              disabled: true,
            },
            { type: "divider" },
            {
              key: "release",
              danger: true,
              label: tipped(
                s === "creating" ? t("instances.actions.cancelCreate") : t("instances.actions.releaseMenu"),
                canRelease ? undefined : copy.releaseNeedsStopped,
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
