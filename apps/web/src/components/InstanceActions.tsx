/**
 * 实例操作组:开机/关机/更多(重启·事件·预留项·释放)。
 * 铁律 #2:条目永不隐藏,灰置用 antd Tooltip 说明前置条件;P1 预留项(无卡模式/保存镜像/
 * 转包年包月)可见但禁用,注「即将上线」—— 有 roadmap 背书才预留,不放假功能。
 * 铁律 #4:释放多级防护(复述名称+ID、勾选确认才解锁红色按钮)。
 */

import { DownOutlined } from "@ant-design/icons";
import type { InstanceOut } from "@superdl/api-client";
import { copy } from "@superdl/ui";
import { App, Button, Checkbox, Dropdown, Modal, Space, Tooltip, Typography } from "antd";
import { useState } from "react";

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
  const [checked, setChecked] = useState(false);
  const { message } = App.useApp();
  const creating = instance.status === "creating";
  const release = useReleaseInstance({
    onSuccess: () => {
      message.success(creating ? "已取消创建" : "实例已开始释放");
      onClose();
      onReleased?.();
    },
  });
  return (
    <Modal
      title={creating ? "取消创建" : "释放实例"}
      open={open}
      onCancel={() => {
        setChecked(false);
        onClose();
      }}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            danger
            type="primary"
            disabled={!checked}
            loading={release.isPending}
            onClick={() => release.mutate(instance.uuid)}
          >
            {creating ? "确认取消" : "确认释放"}
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph>
        {creating ? "即将取消创建中的实例 " : "即将释放实例 "}
        <Typography.Text strong>
          {instance.name}({instance.uuid.slice(0, 8)})
        </Typography.Text>
        ,此操作不可恢复。
        {creating ? "创建中未开始计费,取消不产生 GPU 时费。" : null}
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
      title: "确认关机?",
      content: copy.stopConfirm,
      okText: "关机",
      onOk: async () => {
        await stop.mutateAsync(instance.uuid);
        message.success("已下发关机");
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
          开机
        </Button>
      </Tooltip>
      <Tooltip title={canStop ? undefined : copy.stopNeedsRunning}>
        <Button size="small" disabled={!canStop} onClick={confirmStop}>
          关机
        </Button>
      </Tooltip>
      <Dropdown
        menu={{
          items: [
            {
              key: "restart",
              label: tipped("重启", canRestart ? undefined : copy.stopNeedsRunning),
              disabled: !canRestart,
            },
            { key: "events", label: "事件记录" },
            { type: "divider" },
            {
              key: "cardless",
              label: tipped("无卡模式开机", copy.comingSoon),
              disabled: true,
            },
            {
              key: "save-image",
              label: tipped("保存镜像", copy.comingSoon),
              disabled: true,
            },
            {
              key: "to-period",
              label: tipped("转包年包月", copy.comingSoon),
              disabled: true,
            },
            { type: "divider" },
            {
              key: "release",
              danger: true,
              label: tipped(
                s === "creating" ? "取消创建" : "释放实例",
                canRelease ? undefined : copy.releaseNeedsStopped,
              ),
              disabled: !canRelease,
            },
          ],
          onClick: ({ key }) => {
            if (key === "restart") {
              modal.confirm({
                title: "确认重启?",
                content: "重启期间实例短暂不可用,计费在关机瞬间出尾账、开机后重新计时。",
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
          更多 <DownOutlined />
        </Button>
      </Dropdown>
      <ReleaseModal instance={instance} open={releaseOpen} onClose={() => setReleaseOpen(false)} />
    </Space>
  );
}
