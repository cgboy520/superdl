/**
 * 实例操作组:开机/关机/更多(重启·事件·释放)。
 * 铁律 #2:条目永不隐藏,灰置带 tooltip 说明前置条件。
 * 铁律 #4:释放多级防护(复述名称+ID、勾选确认才解锁红色按钮)。
 */

import { DownOutlined } from "@ant-design/icons";
import type { InstanceOut } from "@superdl/api-client";
import { copy } from "@superdl/ui";
import { App, Button, Checkbox, Dropdown, Modal, Space, Typography } from "antd";
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
  const release = useReleaseInstance({
    onSuccess: () => {
      message.success("实例已开始释放");
      onClose();
      onReleased?.();
    },
  });
  return (
    <Modal
      title="释放实例"
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
            确认释放
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph>
        即将释放实例{" "}
        <Typography.Text strong>
          {instance.name}({instance.uuid.slice(0, 8)})
        </Typography.Text>
        ,此操作不可恢复。
      </Typography.Paragraph>
      <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>
        {copy.releaseConfirmChecklist}
      </Checkbox>
    </Modal>
  );
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
  const canRelease = s === "stopped" || s === "frozen" || s === "failed";

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
      <Button
        size="small"
        disabled={!canStart}
        title={canStart ? undefined : startTip}
        loading={start.isPending}
        onClick={() => start.mutate(instance.uuid)}
      >
        开机
      </Button>
      <Button
        size="small"
        disabled={!canStop}
        title={canStop ? undefined : copy.stopNeedsRunning}
        onClick={confirmStop}
      >
        关机
      </Button>
      <Dropdown
        menu={{
          items: [
            {
              key: "restart",
              label: <span title={canRestart ? undefined : copy.stopNeedsRunning}>重启</span>,
              disabled: !canRestart,
            },
            { key: "events", label: "事件记录" },
            {
              key: "release",
              danger: true,
              label: <span title={canRelease ? undefined : copy.releaseNeedsStopped}>释放实例</span>,
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
      <ReleaseModal
        instance={instance}
        open={releaseOpen}
        onClose={() => setReleaseOpen(false)}
      />
    </Space>
  );
}
