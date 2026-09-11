import {
  imageCacheStatusMap,
  fontSize,
  formatDateTime,
  layout,
  metaOf,
  POLL,
  useAutoRefresh,
  type ImageCacheStatus,
} from "@superdl/ui";
import { PageContainer, TableErrorEmpty, useConfirm } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
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
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type ImageNodeRow,
  type ImageRow,
  isApiError,
  useAdminImages,
  useClusterStatus,
  useCreateImage,
  useDeleteImage,
  useImageNodes,
  usePrewarmImage,
  useUpdateImage,
} from "../../api";
import { useApiErrorText } from "@superdl/ui";
import { ReasonAction } from "../../components/ReasonAction";
import { RowMoreMenu } from "../../components/RowMoreMenu";
import { canWriteOps, useAdminRole } from "../../stores/auth";

export const Route = createFileRoute("/_app/images")({
  // image:展开该镜像的节点缓存面板(告警 / 节点页可直链到失败节点清单)
  validateSearch: (search: Record<string, unknown>): { image?: number } => ({
    image: Number.isInteger(Number(search.image)) && Number(search.image) > 0 ? Number(search.image) : undefined,
  }),
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

/** 行展开:每节点缓存明细(展开期间 30s 轮询) */
function ImageNodesPanel({ imageId }: { imageId: number }) {
  const { t } = useTranslation(["admin", "shared"]);
  const { data, isError, error, refetch } = useImageNodes(imageId, { refetchInterval: POLL.steady });
  return (
    <Table<ImageNodeRow>
      size="small"
      rowKey="node_name"
      dataSource={data ?? []}
      pagination={false}
      locale={{
        emptyText: (
          <TableErrorEmpty
            isError={isError}
            isForbidden={isApiError(error) && error.status === 403}
            onRetry={() => void refetch()}
          >
            {t("images.nodesEmpty")}
          </TableErrorEmpty>
        ),
      }}
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
          render: (v: string | null) => (v ? formatDateTime(v) : "-"),
        },
        {
          title: t("images.colUpdatedAt"),
          dataIndex: "updated_at",
          render: (v: string) => formatDateTime(v),
        },
      ]}
    />
  );
}

function ImagesPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  // 镜像清单轮询(拉取进度在变),可暂停;页头出新鲜度条
  const autoRefresh = useAutoRefresh(POLL.ticket);
  const {
    data: images,
    queryKey,
    dataUpdatedAt,
    isLoading,
    isError,
    isRefetching,
    error,
    refetch,
  } = useAdminImages({ refetchInterval: autoRefresh.refetchInterval });
  const [editing, setEditing] = useState<ImageRow | "new" | null>(null);
  // 新建镜像默认仓库前缀:取平台配置的 Harbor 地址与项目
  const { data: cluster } = useClusterStatus();
  const registryPrefix = cluster?.config.registry_host
    ? `${cluster.config.registry_host}/${cluster.config.registry_project ?? "superdl"}/`
    : "harbor.example.com/superdl/";
  const [form] = Form.useForm<ImageFormValues>();
  const confirm = useConfirm();
  // 展开行入 URL(?image=):失败计数标可点开对应节点面板
  const navigate = useNavigate({ from: "/images" });
  const expandedImage = Route.useSearch({ select: (s) => s.image });
  const setExpandedImage = (id: number | undefined) =>
    void navigate({ to: "/images", replace: true, search: (prev) => ({ ...prev, image: id }) });

  const refresh = () => void qc.invalidateQueries({ queryKey });
  const create = useCreateImage({
    mutation: {
      onSuccess: () => {
        message.success(t("images.created"));
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("common.createFailed"))),
    },
  });
  const update = useUpdateImage({
    mutation: {
      onSuccess: (_d, v) => {
        // 预热开关与整表保存共用本 mutation,反馈文案按补丁形态分开
        const toggleOnly = Object.keys(v.data).length === 1 && "prewarm_enabled" in v.data;
        message.success(t(toggleOnly ? "images.prewarmToggled" : "images.saved"));
        setEditing(null);
        refresh();
      },
      onError: (e) => message.error(errText(e, t("common.saveFailed"))),
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
      // ImageUpdate 无 reason 字段
      update.mutate({ imageId: editing.id, data: values });
    }
  };

  return (
    <PageContainer
      width="full"
      title={t("menu.images")}
      freshness={{
        updatedAt: dataUpdatedAt,
        intervalMs: autoRefresh.intervalMs,
        paused: autoRefresh.paused,
        onTogglePause: autoRefresh.toggle,
        onRefresh: () => void refetch(),
        refreshing: isRefetching,
      }}
      extra={
        <Tooltip title={writable ? "" : t("common.readonlyNoCreate")}>
          <Button type="primary" disabled={!writable} onClick={() => openEdit("new")}>
            {t("images.newImage")}
          </Button>
        </Tooltip>
      }
    >
    <Card>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        title={t("images.prewarmInfo")}
        description={t("images.prewarmInfoDesc")}
      />
      <Table<ImageRow>
        scroll={{ x: 1100 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="id"
        loading={isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        dataSource={images ?? []}
        pagination={false}
        expandable={{
          expandedRowRender: (r) => <ImageNodesPanel imageId={r.id} />,
          expandedRowKeys: expandedImage != null ? [expandedImage] : [],
          onExpand: (open, r) => setExpandedImage(open ? r.id : undefined),
        }}
        columns={[
          {
            title: t("images.colFramework"),
            fixed: "left",
            width: 180,
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
                  // 行级 loading
                  loading={update.isPending && update.variables?.imageId === r.id}
                  onChange={(on) => {
                    // 关闭预热影响新节点的秒级启动承诺:L1 确认;开启直接生效
                    if (on) {
                      update.mutate({ imageId: r.id, data: { prewarm_enabled: true } });
                      return;
                    }
                    confirm({
                      title: t("images.prewarmOffTitle", { name: `${r.framework} ${r.framework_version}` }),
                      consequences: [t("images.prewarmOffBody")],
                      okText: t("images.prewarmOffOk"),
                      onOk: () => update.mutate({ imageId: r.id, data: { prewarm_enabled: false } }),
                    });
                  }}
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
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                    {r.coverage.cached}/{r.coverage.total}
                  </Typography.Text>
                  {r.failed_nodes > 0 && (
                    <Tag
                      color="red"
                      style={{ cursor: "pointer" }}
                      role="button"
                      tabIndex={0}
                      onClick={() => setExpandedImage(r.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") setExpandedImage(r.id);
                      }}
                    >
                      {t("images.failedCount", { count: r.failed_nodes })}
                    </Tag>
                  )}
                </Space>
              );
            },
          },
          {
            title: t("skus.colActions"),
            width: 220,
            fixed: "right",
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
                <Tooltip title={writable ? "" : t("common.readonlyNoEdit")}>
                  <Button size="small" disabled={!writable} onClick={() => openEdit(r)}>
                    {t("skus.edit")}
                  </Button>
                </Tooltip>
                <RowMoreMenu>
                  <ReasonAction
                    label={t("images.delete")}
                    type="text"
                    target={`${r.framework} ${r.framework_version}`}
                    title={t("images.deleteTitle")}
                    confirmText={t("images.deleteConfirm", { name: `${r.framework} ${r.framework_version}` })}
                    danger
                    disabled={!writable}
                    disabledReason={t("images.readonlyNoDelete")}
                    onSubmit={async (reason) => {
                      await del.mutateAsync({ imageId: r.id, data: { reason } });
                    }}
                  />
                </RowMoreMenu>
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
        width="min(480px, 100vw)"
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
    </PageContainer>
  );
}
