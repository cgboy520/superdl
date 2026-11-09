/** Images users uploaded: owner, state (with the import failure), architecture, size, dev-box readiness, tenant sharing; admins delete one with a reason (refused while an unreleased instance uses it). */

import type { AdminCustomImageOut } from "@superdl/api-client";
import { customImageStatusMap, fontSize, formatBytes, formatDateTime, useApiErrorText } from "@superdl/ui";
import { StatusTag, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Card, Space, Table, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useAdminCustomImages, useAdminDeleteCustomImage } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { TenantLink } from "../../components/TenantLink";

export function UserImagesCard({ writable }: { writable: boolean }) {
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
