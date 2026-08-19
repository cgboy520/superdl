import { adminColors } from "@superdl/ui";
import { createFileRoute } from "@tanstack/react-router";
import { Alert, Card, Table, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";

import { type NodeRow, useNodes } from "../../api";

export const Route = createFileRoute("/_app/nodes")({
  component: NodesPage,
});

function GpuGrid({ node }: { node: NodeRow }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {Array.from({ length: node.gpu_total }, (_, i) => {
        const used = i < node.gpu_used;
        return (
          <Tooltip
            key={i}
            title={`GPU ${i} · ${used ? "已租" : "空闲"}(util/显存/温度经 Grafana 查看)`}
          >
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 6,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                background: used ? adminColors.dataAccent : "#1E293B",
                color: used ? "#0B1220" : "#64748B",
                fontWeight: 600,
              }}
            >
              {i}
            </div>
          </Tooltip>
        );
      })}
    </div>
  );
}

function NodesPage() {
  const { data } = useNodes();
  const nodes: NodeRow[] = data ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const node = nodes.find((n) => n.name === selected) ?? nodes[0];

  return (
    <>
      <Card title="节点">
        <Table<NodeRow>
          scroll={{ x: 800 }}
          rowKey="name"
          dataSource={nodes}
          pagination={false}
          onRow={(r) => ({ onClick: () => setSelected(r.name), style: { cursor: "pointer" } })}
          columns={[
            { title: "节点", dataIndex: "name" },
            {
              title: "池",
              dataIndex: "pool_label",
              render: (v: string) => <Tag color="cyan">{v}</Tag>,
            },
            {
              title: "GPU",
              render: (_, r) => `${r.gpu_model} × ${r.gpu_total}`,
            },
            { title: "已用", dataIndex: "gpu_used" },
            {
              title: "状态",
              dataIndex: "status",
              render: (v: string) => (
                <Tag color={v === "Ready" ? "green" : v === "Cordoned" ? "orange" : "red"}>{v}</Tag>
              ),
            },
            {
              title: "操作",
              render: () => (
                <Tooltip title="cordon/drain 经集群运维通道执行(人工事项 Runbook),此处只读">
                  <Typography.Text type="secondary">cordon · drain</Typography.Text>
                </Tooltip>
              ),
            },
          ]}
        />
      </Card>
      {node && (
        <Card title={`每卡视图 · ${node.name}`} style={{ marginTop: 16 }}>
          <GpuGrid node={node} />
        </Card>
      )}
      <Card title="节点历史曲线(Grafana)" style={{ marginTop: 16 }}>
        <Alert
          type="info"
          showIcon
          message="生产环境此处嵌入 Grafana DCGM 大盘(iframe)"
          description="部署要求:Grafana 13 开启 allow_embedding=true,经反向代理注入只读 Viewer 身份;面板以社区 24450 为底改造。本地开发环境无 Grafana,显示此占位。"
        />
      </Card>
    </>
  );
}
