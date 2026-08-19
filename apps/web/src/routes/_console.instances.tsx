/** 容器实例列表(默认落地页):策略提示条 + 全字段表格 + 常显灰置操作。5s 轮询。 */

import { CodeOutlined } from "@ant-design/icons";
import { type InstanceOut } from "@superdl/api-client";
import { copy, formatDateTime, formatHourlyPrice, tabularNums } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Input,
  Popover,
  Space,
  Table,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";

import { useRenameInstance } from "../api/mutations";
import { useInstanceAccess, useInstances } from "../api/queries";
import { CopyButton, InstanceStatusBadge, TierTag } from "../components/common";
import { InstanceActions } from "../components/InstanceActions";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/instances")({
  beforeLoad: requireAuth,
  component: InstancesPage,
});

function SshCell({ instance }: { instance: InstanceOut }) {
  const running = instance.status === "running";
  const { data: access } = useInstanceAccess(instance.uuid, { enabled: running });
  if (!running) {
    return (
      <Tooltip title={copy.jupyterNeedsRunning}>
        <Space size={4}>
          <Button size="small" disabled icon={<CodeOutlined />}>
            SSH
          </Button>
          <Button size="small" disabled>
            JupyterLab
          </Button>
        </Space>
      </Tooltip>
    );
  }
  return (
    <Space size={4}>
      {access ? <CopyButton text={access.ssh_command} label="SSH" /> : null}
      <Button
        size="small"
        type="link"
        disabled={!access}
        onClick={() => window.open(access?.jupyter_url, "_blank")}
      >
        JupyterLab
      </Button>
    </Space>
  );
}

function NameCell({ instance }: { instance: InstanceOut }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(instance.name);
  const rename = useRenameInstance();
  const { message } = App.useApp();
  if (editing) {
    return (
      <Input
        size="small"
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => setEditing(false)}
        onPressEnter={async () => {
          await rename.mutateAsync({ uuid: instance.uuid, name: value });
          setEditing(false);
          message.success("已改名");
        }}
        style={{ width: 160 }}
      />
    );
  }
  return (
    <Space orientation="vertical" size={0}>
      <Typography.Text
        strong
        style={{ cursor: "pointer" }}
        title="点击改名"
        onClick={() => setEditing(true)}
      >
        {instance.name}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {instance.uuid.slice(0, 12)}
      </Typography.Text>
    </Space>
  );
}

function InstancesPage() {
  const navigate = useNavigate();
  const { data: instances, isLoading } = useInstances({ refetchInterval: 5_000 });

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          容器实例
        </Typography.Title>
        <Link to="/market">
          <Button type="primary">租用新实例</Button>
        </Link>
      </Space>
      <Alert type="info" showIcon message={copy.freezePolicy} />
      <Table<InstanceOut>
        rowKey="uuid"
        loading={isLoading}
        dataSource={instances ?? []}
        pagination={false}
        locale={{ emptyText: "还没有实例,去算力市场租一台吧" }}
        columns={[
          { title: "名称 / ID", render: (_, r) => <NameCell instance={r} /> },
          {
            title: "状态",
            render: (_, r) => (
              <InstanceStatusBadge status={r.status} frozenDeadline={r.frozen_deadline} />
            ),
          },
          {
            title: "规格",
            render: (_, r) => (
              <Popover
                content={
                  <Space orientation="vertical" size={2}>
                    <span>{r.spec["sku_name"] as string}</span>
                    <span>
                      {r.spec["vcpu"] as number} vCPU / {r.spec["mem_gb"] as number}G 内存 /
                      实例盘 {r.spec["disk_gb"] as number}G
                    </span>
                    <span>镜像:{r.image_ref}</span>
                    <span>创建于 {formatDateTime(r.created_at)}</span>
                  </Space>
                }
              >
                <Space>
                  <span>
                    {r.spec["gpu_model"] as string} × {r.gpu_count}
                  </span>
                  <TierTag tier={r.spec["tier"] as string} />
                </Space>
              </Popover>
            ),
          },
          {
            title: "计费",
            render: (_, r) => (
              <span style={tabularNums}>
                {formatHourlyPrice(r.price_hourly)} × {r.gpu_count} 卡
              </span>
            ),
          },
          { title: "连接", render: (_, r) => <SshCell instance={r} /> },
          {
            title: "操作",
            fixed: "right",
            render: (_, r) => (
              <InstanceActions
                instance={r}
                onShowEvents={() =>
                  navigate({
                    to: "/instances/$uuid",
                    params: { uuid: r.uuid },
                    search: { tab: "events" },
                  })
                }
              />
            ),
          },
        ]}
        onRow={(r) => ({
          onDoubleClick: () => navigate({ to: "/instances/$uuid", params: { uuid: r.uuid } }),
        })}
      />
    </Space>
  );
}
