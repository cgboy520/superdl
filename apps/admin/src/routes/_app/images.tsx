import { imageCacheStatusMap, metaOf, type ImageCacheStatus } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Progress,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type ImageNodeRow,
  type ImageRow,
  isApiError,
  useAdminImages,
  useCreateImage,
  useDeleteImage,
  useImageNodes,
  usePrewarmImage,
  useUpdateImage,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/images")({
  component: ImagesPage,
});

interface ImageFormValues {
  framework: string;
  framework_version: string;
  python_version: string;
  cuda_version: string;
  image_ref: string;
  sort: number;
  prewarm_enabled: boolean;
}

/** 行展开:该镜像的每节点缓存明细(展开期间 10s 轮询看拉取进度) */
function ImageNodesPanel({ imageId }: { imageId: number }) {
  const { t } = useTranslation();
  const { data } = useImageNodes(imageId, { refetchInterval: 10_000 });
  return (
    <Table<ImageNodeRow>
      size="small"
      rowKey="node_name"
      dataSource={data ?? []}
      pagination={false}
      locale={{ emptyText: "暂无节点记录(巡检每 60s 铺行,或该镜像已关闭预热)" }}
      columns={[
        { title: "节点", dataIndex: "node_name" },
        {
          title: "缓存状态",
          dataIndex: "status",
          render: (v: ImageCacheStatus) => {
            const meta = metaOf(imageCacheStatusMap, v);
            return meta ? <Badge status={meta.badge} text={t(meta.labelKey)} /> : v;
          },
        },
        {
          title: "失败原因",
          dataIndex: "last_error",
          width: 320,
          render: (v: string | null) =>
            v ? (
              <Tooltip title={v}>
                <Typography.Text type="danger" ellipsis style={{ maxWidth: 300 }}>
                  {v}
                </Typography.Text>
              </Tooltip>
            ) : (
              "-"
            ),
        },
        {
          title: "最近确认",
          dataIndex: "checked_at",
          render: (v: string | null) => (v ? dayjs(v).format("MM-DD HH:mm") : "-"),
        },
        {
          title: "更新时间",
          dataIndex: "updated_at",
          render: (v: string) => dayjs(v).format("MM-DD HH:mm"),
        },
      ]}
    />
  );
}

function ImagesPage() {
  // 深色主题下必须走 useApp 实例:静态 message 拿不到 ConfigProvider token
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data: images, refetch, queryKey } = useAdminImages({ refetchInterval: 15_000 });
  const [editing, setEditing] = useState<ImageRow | "new" | null>(null);
  const [form] = Form.useForm<ImageFormValues>();

  const refresh = () => {
    void qc.invalidateQueries({ queryKey });
    void refetch();
  };
  const create = useCreateImage({
    mutation: {
      onSuccess: () => {
        message.success("镜像已创建,巡检将在 1 分钟内开始各节点预热");
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "创建失败"),
    },
  });
  const update = useUpdateImage({
    mutation: {
      onSuccess: () => {
        message.success("已保存");
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "保存失败"),
    },
  });
  const del = useDeleteImage({
    mutation: { onSuccess: refresh },
  });
  const prewarm = usePrewarmImage({
    mutation: {
      onSuccess: (r) => {
        message.success(`已触发预热,入队 ${r.enqueued} 个节点任务`);
        refresh();
      },
      onError: (e) => message.error(isApiError(e) ? e.message : "触发失败"),
    },
  });

  const openEdit = (img: ImageRow | "new") => {
    setEditing(img);
    if (img === "new") {
      form.resetFields();
      form.setFieldsValue({
        sort: 0,
        prewarm_enabled: true,
        image_ref: "registry.superdl.local/",
      });
    } else {
      form.setFieldsValue(img);
    }
  };

  const submit = async () => {
    const values = await form.validateFields();
    if (editing === "new") {
      create.mutate({ data: values });
    } else if (editing) {
      update.mutate({ imageId: editing.id, data: values });
    }
  };

  return (
    <Card
      title="镜像与预热"
      extra={
        <Tooltip title={writable ? "" : "只读角色不可创建"}>
          <Button type="primary" disabled={!writable} onClick={() => openEdit("new")}>
            新建镜像
          </Button>
        </Tooltip>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="预热机制"
        description="开启预热的镜像会被巡检自动分发到全部就绪节点(每节点定点拉取);覆盖率达标后用户创建页展示「预热镜像,秒级启动」。新节点加入后 1 分钟内自动纳入。镜像一律钉版本 tag,发布 SOP 见 deploy/cluster/runbooks/image-prewarm.md。"
      />
      <Table<ImageRow>
        scroll={{ x: 1100 }}
        rowKey="id"
        dataSource={images ?? []}
        pagination={false}
        expandable={{
          expandedRowRender: (r) => <ImageNodesPanel imageId={r.id} />,
        }}
        columns={[
          {
            title: "框架",
            render: (_, r) => `${r.framework} ${r.framework_version}`,
          },
          { title: "Python", dataIndex: "python_version" },
          { title: "CUDA", dataIndex: "cuda_version" },
          {
            title: "镜像地址",
            dataIndex: "image_ref",
            width: 320,
            render: (v: string) => (
              <Typography.Text copyable ellipsis style={{ maxWidth: 300 }}>
                {v}
              </Typography.Text>
            ),
          },
          {
            title: "预热",
            dataIndex: "prewarm_enabled",
            render: (v: boolean, r) => (
              <Tooltip title={writable ? "" : "只读角色不可操作"}>
                <Switch
                  checked={v}
                  disabled={!writable}
                  onChange={(on) =>
                    update.mutate({ imageId: r.id, data: { prewarm_enabled: on } })
                  }
                />
              </Tooltip>
            ),
          },
          {
            title: "节点覆盖",
            render: (_, r) => {
              if (!r.prewarm_enabled) return <Tag>已关闭</Tag>;
              if (r.coverage.total === 0) return <Tag color="default">待巡检</Tag>;
              return (
                <Space>
                  <Progress
                    percent={r.coverage.pct}
                    size="small"
                    style={{ width: 120 }}
                    status={r.failed_nodes > 0 ? "exception" : undefined}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {r.coverage.cached}/{r.coverage.total}
                  </Typography.Text>
                  {r.failed_nodes > 0 && <Tag color="red">失败 {r.failed_nodes}</Tag>}
                </Space>
              );
            },
          },
          {
            title: "操作",
            width: 240,
            render: (_, r) => (
              <Space>
                <Tooltip title={writable ? "重派全部未缓存节点" : "只读角色不可操作"}>
                  <Button
                    size="small"
                    disabled={!writable || !r.prewarm_enabled}
                    loading={prewarm.isPending && prewarm.variables?.imageId === r.id}
                    onClick={() => prewarm.mutate({ imageId: r.id })}
                  >
                    立即预热
                  </Button>
                </Tooltip>
                <Tooltip title={writable ? "" : "只读角色不可编辑"}>
                  <Button size="small" disabled={!writable} onClick={() => openEdit(r)}>
                    编辑
                  </Button>
                </Tooltip>
                <ReasonAction
                  label="删除"
                  title="删除镜像"
                  confirmText={`删除「${r.framework} ${r.framework_version}」目录条目并清空各节点缓存记录;运行中实例不受影响。`}
                  danger
                  disabled={!writable}
                  disabledReason="只读角色不可删除"
                  onSubmit={async (reason) => {
                    await del.mutateAsync({ imageId: r.id, data: { reason } });
                  }}
                />
              </Space>
            ),
          },
        ]}
      />
      <Drawer
        title={
          editing === "new"
            ? "新建镜像"
            : `编辑镜像 · ${typeof editing === "object" && editing ? editing.framework : ""}`
        }
        open={editing !== null}
        onClose={() => setEditing(null)}
        width={480}
        extra={
          <Button type="primary" loading={create.isPending || update.isPending} onClick={submit}>
            提交
          </Button>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="framework" label="框架" rules={[{ required: true }]}>
            <Input placeholder="如 PyTorch / TensorFlow / Miniconda" />
          </Form.Item>
          <Form.Item name="framework_version" label="框架版本" rules={[{ required: true }]}>
            <Input placeholder="如 2.9.0" />
          </Form.Item>
          <Form.Item name="python_version" label="Python 版本" rules={[{ required: true }]}>
            <Input placeholder="如 3.12" />
          </Form.Item>
          <Form.Item name="cuda_version" label="CUDA 版本" rules={[{ required: true }]}>
            <Input placeholder="如 12.8" />
          </Form.Item>
          {editing !== "new" && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="变更镜像地址将清空全部节点缓存记录并按新地址重新预热"
            />
          )}
          <Form.Item
            name="image_ref"
            label="镜像地址(钉版本 tag,禁止 latest)"
            rules={[
              { required: true, min: 3 },
              {
                validator: (_, v: string) =>
                  v?.endsWith(":latest") || (v && !v.includes(":"))
                    ? Promise.reject(new Error("必须钉具体版本 tag(latest 不参与 P2P 缓存)"))
                    : Promise.resolve(),
              },
            ]}
          >
            <Input placeholder="registry.superdl.local/pytorch:2.9.0-cu128" />
          </Form.Item>
          <Form.Item name="sort" label="排序(同框架内)">
            <InputNumber min={0} max={9999} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="prewarm_enabled" label="参与预热" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
