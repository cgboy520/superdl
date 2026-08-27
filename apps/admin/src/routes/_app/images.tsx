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
  useAdminImages,
  useClusterStatus,
  useCreateImage,
  useDeleteImage,
  useImageNodes,
  usePrewarmImage,
  useUpdateImage,
} from "../../api";
import { useApiErrorText } from "../../lib/apiError";
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
  const { t } = useTranslation(["admin", "shared"]);
  const { data } = useImageNodes(imageId, { refetchInterval: 10_000 });
  return (
    <Table<ImageNodeRow>
      size="small"
      rowKey="node_name"
      dataSource={data ?? []}
      pagination={false}
      locale={{ emptyText: t("images.nodesEmpty") }}
      columns={[
        { title: t("nodes.colNode"), dataIndex: "node_name" },
        {
          title: t("images.colCacheStatus"),
          dataIndex: "status",
          render: (v: ImageCacheStatus) => {
            const meta = metaOf(imageCacheStatusMap, v);
            return meta ? <Badge status={meta.badge} text={t(meta.labelKey)} /> : v;
          },
        },
        {
          title: t("nodes.colError"),
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
          title: t("images.colCheckedAt"),
          dataIndex: "checked_at",
          render: (v: string | null) => (v ? dayjs(v).format("MM-DD HH:mm") : "-"),
        },
        {
          title: t("images.colUpdatedAt"),
          dataIndex: "updated_at",
          render: (v: string) => dayjs(v).format("MM-DD HH:mm"),
        },
      ]}
    />
  );
}

function ImagesPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  // 深色主题下必须走 useApp 实例:静态 message 拿不到 ConfigProvider token
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data: images, queryKey } = useAdminImages({ refetchInterval: 15_000 });
  const [editing, setEditing] = useState<ImageRow | "new" | null>(null);
  // 新建镜像的默认仓库前缀:Harbor 地址与平台项目来自平台配置(经集群状态透出,ops 可读)
  const { data: cluster } = useClusterStatus();
  const registryPrefix = cluster?.config.registry_host
    ? `${cluster.config.registry_host}/${cluster.config.registry_project ?? "superdl"}/`
    : "harbor.example.com/superdl/";
  const [form] = Form.useForm<ImageFormValues>();

  const refresh = () => void qc.invalidateQueries({ queryKey });
  const create = useCreateImage({
    mutation: {
      onSuccess: () => {
        message.success(t("images.created"));
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("skus.createFailed"))),
    },
  });
  const update = useUpdateImage({
    mutation: {
      onSuccess: () => {
        message.success(t("images.saved"));
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("skus.saveFailed"))),
    },
  });
  const del = useDeleteImage({
    mutation: { onSuccess: refresh },
  });
  const prewarm = usePrewarmImage({
    mutation: {
      onSuccess: (r) => {
        message.success(t("images.prewarmTriggered", { count: r.enqueued }));
        refresh();
      },
      onError: (e) => message.error(errText(e, t("images.triggerFailed"))),
    },
  });

  const openEdit = (img: ImageRow | "new") => {
    setEditing(img);
    if (img === "new") {
      form.resetFields();
      form.setFieldsValue({
        sort: 0,
        prewarm_enabled: true,
        image_ref: registryPrefix,
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
      title={t("menu.images")}
      extra={
        <Tooltip title={writable ? "" : t("skus.readonlyNoCreate")}>
          <Button type="primary" disabled={!writable} onClick={() => openEdit("new")}>
            {t("images.newImage")}
          </Button>
        </Tooltip>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        title={t("images.prewarmInfo")}
        description={t("images.prewarmInfoDesc")}
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
            title: t("images.colFramework"),
            render: (_, r) => `${r.framework} ${r.framework_version}`,
          },
          { title: "Python", dataIndex: "python_version" },
          { title: "CUDA", dataIndex: "cuda_version" },
          {
            title: t("images.colImageRef"),
            dataIndex: "image_ref",
            width: 320,
            render: (v: string) => (
              <Typography.Text copyable ellipsis style={{ maxWidth: 300 }}>
                {v}
              </Typography.Text>
            ),
          },
          {
            title: t("images.colPrewarm"),
            dataIndex: "prewarm_enabled",
            render: (v: boolean, r) => (
              <Tooltip title={writable ? "" : t("nodes.readonlyNoOp")}>
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
            title: t("images.colCoverage"),
            render: (_, r) => {
              if (!r.prewarm_enabled) return <Tag>{t("images.disabled")}</Tag>;
              if (r.coverage.total === 0) return <Tag color="default">{t("images.awaitingPatrol")}</Tag>;
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
                  {r.failed_nodes > 0 && <Tag color="red">{t("images.failedCount", { count: r.failed_nodes })}</Tag>}
                </Space>
              );
            },
          },
          {
            title: t("skus.colActions"),
            width: 240,
            render: (_, r) => (
              <Space>
                <Tooltip title={writable ? t("images.prewarmTip") : t("nodes.readonlyNoOp")}>
                  <Button
                    size="small"
                    disabled={!writable || !r.prewarm_enabled}
                    loading={prewarm.isPending && prewarm.variables?.imageId === r.id}
                    onClick={() => prewarm.mutate({ imageId: r.id })}
                  >
                    {t("images.prewarmNow")}
                  </Button>
                </Tooltip>
                <Tooltip title={writable ? "" : t("skus.readonlyNoEdit")}>
                  <Button size="small" disabled={!writable} onClick={() => openEdit(r)}>
                    {t("skus.edit")}
                  </Button>
                </Tooltip>
                <ReasonAction
                  label={t("images.delete")}
                  title={t("images.deleteTitle")}
                  confirmText={t("images.deleteConfirm", { name: `${r.framework} ${r.framework_version}` })}
                  danger
                  disabled={!writable}
                  disabledReason={t("images.readonlyNoDelete")}
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
            ? t("images.newImage")
            : t("images.editTitle", { name: typeof editing === "object" && editing ? editing.framework : "" })
        }
        open={editing !== null}
        onClose={() => setEditing(null)}
        width={480}
        extra={
          <Button type="primary" loading={create.isPending || update.isPending} onClick={submit}>
            {t("skus.submit")}
          </Button>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="framework" label={t("images.colFramework")} rules={[{ required: true }]}>
            <Input placeholder={t("images.frameworkPlaceholder")} />
          </Form.Item>
          <Form.Item name="framework_version" label={t("images.frameworkVersionLabel")} rules={[{ required: true }]}>
            <Input placeholder={t("images.versionPlaceholder")} />
          </Form.Item>
          <Form.Item name="python_version" label={t("images.pythonVersionLabel")} rules={[{ required: true }]}>
            <Input placeholder={t("images.pythonPlaceholder")} />
          </Form.Item>
          <Form.Item name="cuda_version" label={t("images.cudaVersionLabel")} rules={[{ required: true }]}>
            <Input placeholder={t("images.cudaPlaceholder")} />
          </Form.Item>
          {editing !== "new" && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              title={t("images.refChangeWarn")}
            />
          )}
          <Form.Item
            name="image_ref"
            label={t("images.imageRefLabel")}
            rules={[
              { required: true, min: 3 },
              {
                validator: (_, v: string) =>
                  v && !/@sha256:[0-9a-f]{64}$/.test(v)
                    ? Promise.reject(new Error(t("images.tagRule")))
                    : Promise.resolve(),
              },
            ]}
          >
            <Input placeholder={`${registryPrefix}pytorch:2.13.0-cu132-py313@sha256:…`} />
          </Form.Item>
          <Form.Item name="sort" label={t("images.sortLabel")}>
            <InputNumber min={0} max={9999} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="prewarm_enabled" label={t("images.prewarmEnabledLabel")} valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  );
}
