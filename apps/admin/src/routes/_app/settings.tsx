/**
 * 系统设置(ui-ux-spec §4.1 信息架构第 7 屏):
 * - 策略参数:env 默认 + DB 覆盖,保存需原因,即时生效并同步 GET /policies
 * - 公告发布:announcement 站内信群发全体 active 租户
 */

import { adminColors, formatDateTime } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
import { useState } from "react";

import {
  isApiError,
  useAdminPolicies,
  usePublishAnnouncement,
  useUpdatePolicies,
} from "../../api";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/settings")({
  component: SettingsPage,
});

const POLICY_LABELS: Record<string, { label: string; unit: string; hint?: string }> = {
  disk_price_gb_month: { label: "数据盘单价", unit: "元/GB·月", hint: "建盘时快照,调价只影响新盘" },
  disk_min_gb: { label: "数据盘最小容量", unit: "GB" },
  disk_max_gb: { label: "数据盘最大容量", unit: "GB" },
  disk_grace_days: { label: "欠费宽限(数据盘)", unit: "天", hint: "宽限到期转冻结" },
  disk_frozen_days: { label: "冻结保留(数据盘)", unit: "天", hint: "冻结到期回收擦除" },
  freeze_grace_hours: { label: "欠费冻结时长(实例)", unit: "小时", hint: "冻结到期回收实例盘" },
  low_balance_warn_hours: { label: "默认余额预警阈值", unit: "小时" },
};

function PoliciesTab() {
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data, queryKey, isLoading } = useAdminPolicies();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reasonForm] = Form.useForm<{ reason: string }>();

  const update = useUpdatePolicies({
    mutation: {
      onSuccess: () => {
        message.success("策略已更新,即时生效");
        setDraft({});
        setReasonOpen(false);
        reasonForm.resetFields();
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "保存失败"),
    },
  });

  const rows = Object.entries(POLICY_LABELS).map(([key, meta]) => ({
    key,
    ...meta,
    effective: data?.effective[key] ?? "",
    overridden: data?.overrides[key] != null,
    spec: data?.specs[key],
  }));
  const changed = Object.entries(draft).filter(
    ([k, v]) => v !== "" && v !== (data?.effective[k] ?? ""),
  );

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        title="参数保存后即时生效(免重启):用户端价格/倒计时与巡检回收同步跟随。"
      />
      <Table
        rowKey="key"
        size="small"
        loading={isLoading}
        pagination={false}
        scroll={{ x: 760 }}
        dataSource={rows}
        columns={[
          {
            title: "参数",
            render: (_, r) => (
              <>
                {r.label}
                {r.overridden && <Tag style={{ marginLeft: 8 }}>已覆盖</Tag>}
                {r.hint && (
                  <div style={{ color: adminColors.textSecondary, fontSize: 12 }}>{r.hint}</div>
                )}
              </>
            ),
          },
          { title: "当前生效值", dataIndex: "effective", width: 130 },
          { title: "单位", dataIndex: "unit", width: 110 },
          {
            title: "取值范围",
            width: 140,
            render: (_, r) => (r.spec ? `${r.spec.min} ~ ${r.spec.max}` : "-"),
          },
          {
            title: "新值",
            width: 160,
            render: (_, r) => (
              <InputNumber
                size="small"
                style={{ width: 140 }}
                disabled={!writable}
                stringMode
                placeholder={r.effective}
                value={draft[r.key] ?? null}
                onChange={(v) =>
                  setDraft((d) => ({ ...d, [r.key]: v == null ? "" : String(v) }))
                }
              />
            ),
          },
        ]}
      />
      <Tooltip title={writable ? "" : "仅运维/超管可调整策略"}>
        <Button
          type="primary"
          style={{ marginTop: 12 }}
          disabled={!writable || changed.length === 0}
          onClick={() => setReasonOpen(true)}
        >
          保存 {changed.length > 0 ? `${changed.length} 项变更` : ""}(需原因)
        </Button>
      </Tooltip>
      <Modal
        title="确认调整策略参数"
        open={reasonOpen}
        onCancel={() => setReasonOpen(false)}
        okButtonProps={{ loading: update.isPending }}
        onOk={async () => {
          const { reason } = await reasonForm.validateFields();
          update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {changed.map(([k, v]) => (
            <div key={k}>
              {POLICY_LABELS[k]?.label ?? k}:{data?.effective[k]} → <b>{v}</b>
            </div>
          ))}
          <Form form={reasonForm} layout="vertical">
            <Form.Item
              name="reason"
              label="原因(必填,入审计)"
              rules={[{ required: true, min: 2, message: "请填写调整原因(至少 2 字)" }]}
            >
              <Input.TextArea rows={2} placeholder="如:季度调价 / 回收周期运营调整" />
            </Form.Item>
          </Form>
        </Space>
      </Modal>
    </>
  );
}

function AnnouncementTab() {
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const [form] = Form.useForm<{ title: string; content: string }>();
  const [lastPublished, setLastPublished] = useState<{ title: string; at: string; reached: number } | null>(null);

  const publish = usePublishAnnouncement({
    mutation: {
      onSuccess: (d) => {
        const reached = (d as { reached: number }).reached;
        message.success(`公告已发布,触达 ${reached} 位租户`);
        setLastPublished({
          title: form.getFieldValue("title") as string,
          at: new Date().toISOString(),
          reached,
        });
        form.resetFields();
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "发布失败"),
    },
  });

  return (
    <Space orientation="vertical" size={12} style={{ width: "100%", maxWidth: 640 }}>
      <Alert
        type="info"
        showIcon
        title="发布后以站内信(公告类型)触达全部正常状态租户,用户端铃铛与概览页展示。"
      />
      <Form form={form} layout="vertical" disabled={!writable}>
        <Form.Item
          name="title"
          label="标题"
          rules={[{ required: true, min: 2, max: 128, message: "标题 2~128 字" }]}
        >
          <Input placeholder="如:8 月 24 日 02:00–04:00 存储集群维护" />
        </Form.Item>
        <Form.Item
          name="content"
          label="内容"
          rules={[{ required: true, min: 2, max: 2000, message: "内容 2~2000 字" }]}
        >
          <Input.TextArea rows={4} placeholder="说明影响范围、时间窗口与用户需要做什么" />
        </Form.Item>
      </Form>
      <Popconfirm
        title="确认向全部租户发布该公告?"
        onConfirm={async () => {
          const values = await form.validateFields();
          publish.mutate({ data: values });
        }}
        disabled={!writable}
      >
        <Tooltip title={writable ? "" : "仅运维/超管可发布公告"}>
          <Button type="primary" loading={publish.isPending} disabled={!writable}>
            发布公告
          </Button>
        </Tooltip>
      </Popconfirm>
      {lastPublished && (
        <Alert
          type="success"
          showIcon
          title={`「${lastPublished.title}」已于 ${formatDateTime(lastPublished.at)} 发布,触达 ${lastPublished.reached} 位租户`}
        />
      )}
    </Space>
  );
}

function SettingsPage() {
  return (
    <Card>
      <Tabs
        items={[
          { key: "policies", label: "计费与回收策略", children: <PoliciesTab /> },
          { key: "announcement", label: "公告发布", children: <AnnouncementTab /> },
        ]}
      />
    </Card>
  );
}
