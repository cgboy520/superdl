/** User images tab: per-user quota usage against the per-user limits, then every upload (owner, state with the import failure, architecture, size, dev-box readiness, tenant sharing); admins delete one with a reason (refused while an unreleased instance uses it). */

import type { AdminCustomImageOut, CustomImageUsageOut } from "@superdl/api-client";
import { customImageStatusMap, fontSize, formatBytes, formatDateTime, space, useApiErrorText } from "@superdl/ui";
import { StatusTag, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Card, Progress, Space, Table, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useAdminCustomImageUsage, useAdminCustomImages, useAdminDeleteCustomImage } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { TenantLink } from "../../components/TenantLink";

export function UserImagesTab({ writable }: { writable: boolean }) {
  return (
    <Space orientation="vertical" size={space.lg} style={{ display: "flex" }}>
      <UsageCard />
      <UploadsCard writable={writable} />
    </Space>
  );
}

/** Users with images, largest first: count and bytes as the upload quota counts them. */
function UsageCard() {
  const { t } = useTranslation();
  const q = useAdminCustomImageUsage();
  const maxImages = q.data?.max_images ?? 0;
  const maxBytes = q.data?.max_bytes ?? 0;
  return (
    <Card
      size="small"
      title={t("images.usageTitle")}
      extra={
        q.data && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("images.usageLimits", { max: maxImages, size: formatBytes(maxBytes) })}
          </Typography.Text>
        )
      }
    >
      <Table<CustomImageUsageOut>
        rowKey="user_id"
        size="small"
        loading={q.isLoading}
        pagination={(q.data?.users.length ?? 0) > 20 ? { pageSize: 20 } : false}
        dataSource={q.data?.users ?? []}
        locale={{
          emptyText: q.isError ? <TableErrorEmpty isError onRetry={() => void q.refetch()} /> : t("images.userEmpty"),
        }}
        columns={[
          { title: t("images.colUser"), width: 120, render: (_, r) => <TenantLink id={r.user_id} /> },
          { title: t("images.colUsageImages"), width: 140, render: (_, r) => `${r.images} / ${maxImages}` },
          {
            title: t("images.colUsageBytes"),
            render: (_, r) => (
              <Space>
                <Progress
                  percent={maxBytes ? Math.min(100, Math.round((r.bytes * 100) / maxBytes)) : 0}
                  size="small"
                  style={{ width: 160 }}
                  status={maxBytes && r.bytes >= maxBytes ? "exception" : undefined}
                />
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  {`${formatBytes(r.bytes)} / ${formatBytes(maxBytes)}`}
                </Typography.Text>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}

function UploadsCard({ writable }: { writable: boolean }) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const qc = useQueryClient();
  const q = useAdminCustomImages();
  const del = useAdminDeleteCustomImage();
  return (
    <Card size="small" title={t("images.userTitle")}>
      <Table<AdminCustomImageOut>
        rowKey="uuid"
        size="small"
        loading={q.isLoading}
        scroll={{ x: 1100 }}
        pagination={(q.data?.length ?? 0) > 50 ? { pageSize: 50 } : false}
        dataSource={q.data ?? []}
        locale={{
          emptyText: q.isError ? <TableErrorEmpty isError onRetry={() => void q.refetch()} /> : t("images.userEmpty"),
        }}
        columns={[
          { title: t("images.colUser"), width: 80, render: (_, r) => <TenantLink id={r.user_id} /> },
          {
            title: t("images.colUserImage"),
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <Typography.Text className="mono">{`${r.name}:${r.tag}`}</Typography.Text>
                {r.image_ref && (
                  <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }} className="mono" copyable>
                    {r.image_ref}
                  </Typography.Text>
                )}
              </Space>
            ),
          },
          {
            title: t("images.colUserStatus"),
            render: (_, r) => (
              <Space orientation="vertical" size={0} style={{ maxWidth: 280 }}>
                <StatusTag map={customImageStatusMap} value={r.status} variant="badge" />
                {r.status === "failed" && (
                  <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
                    {errText({ message_key: r.error_key, message: r.error_detail ?? undefined }, r.error_key ?? "")}
                  </Typography.Text>
                )}
              </Space>
            ),
          },
          { title: t("images.colArch"), render: (_, r) => r.architecture ?? "—" },
          { title: t("images.colSize"), render: (_, r) => formatBytes(r.size_bytes ?? r.upload_bytes) },
          {
            title: t("images.colDevReady"),
            render: (_, r) =>
              r.status === "ready" ? (r.dev_ready ? t("images.devReadyYes") : t("images.devReadyNo")) : "—",
          },
          {
            title: t("images.colShared"),
            render: (_, r) => (r.shared_with_org ? <Tag variant="filled">{t("images.sharedYes")}</Tag> : "—"),
          },
          { title: t("images.colUploaded"), render: (_, r) => formatDateTime(r.created_at) },
          {
            title: t("images.colActions"),
            render: (_, r) => (
              <ReasonAction
                label={t("images.userDelete")}
                title={t("images.userDeleteTitle", { name: `${r.name}:${r.tag}` })}
                confirmText={t("images.userDeleteConfirm")}
                target={<Typography.Text className="mono">{`#${r.user_id} ${r.name}:${r.tag}`}</Typography.Text>}
                danger
                disabled={!writable || r.status === "deleting" || r.status === "importing"}
                disabledReason={!writable ? t("common.readonlyNoCreate") : t("images.userDeleteBusy")}
                onSubmit={async (reason) => {
                  await del.mutateAsync({ uuid: r.uuid, reason });
                  void qc.invalidateQueries({ queryKey: ["admin", "custom-images"] });
                }}
              />
            ),
          },
        ]}
      />
    </Card>
  );
}
