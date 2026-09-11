/** 全局在线服务表(在线服务页与租户抽屉共用);唯一处置「强制停止」委托当前版本实例的 force-stop。 */

import { fontSize, formatDateTime, metaOf, serviceStatusMap } from "@superdl/ui";
import { HexTag, LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Space, Table, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { type AdminServiceOut, isApiError, useAdminServices, useForceStop } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteOps, useAdminRole } from "../../stores/auth";

function hostOf(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

export function AdminServicesTable({
  userId,
  q,
  includeReleased,
  compact,
}: {
  userId?: number;
  q?: string;
  includeReleased?: boolean;
  /** 抽屉内:小表 + 前 100 条明示截断,不出归属列 */
  compact?: boolean;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const servicesQ = useAdminServices(
    {
      ...(userId ? { user_id: userId } : {}),
      ...(q ? { q } : {}),
      ...(includeReleased ? { include_released: true } : {}),
    },
    compact ? { limit: 100 } : undefined,
  );
  const { data, queryKey, isLoading, isError, error, refetch, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } =
    servicesQ;
  const rows: AdminServiceOut[] = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages[0]?.total ?? null;
  const forceStop = useForceStop();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  return (
    <>
      {compact && total !== null && total > rows.length && (
        <Typography.Text
          type="warning"
          style={{ display: "block", marginBottom: 8, fontSize: fontSize.caption }}
        >
          {t("services.capped", { shown: rows.length, total })}{" "}
          <Link to="/services" search={{ user_id: userId }}>
            {t("services.viewAll")}
          </Link>
        </Typography.Text>
      )}
      <Table<AdminServiceOut>
        size={compact ? "small" : undefined}
        rowKey="slug"
        loading={isLoading}
        pagination={false}
        scroll={compact ? { x: 900, y: 420 } : { x: 1240 }}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        dataSource={rows}
        columns={[
          {
            title: t("services.colService"),
            render: (_, r) => (
              <Space orientation="vertical" size={0}>
                <span>{r.name}</span>
                <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                  {r.slug}
                </Typography.Text>
              </Space>
            ),
          },
          ...(compact ? [] : [tenantColumn<AdminServiceOut>(t("tenants.colOwner"), 90)]),
          {
            title: t("services.colEndpoint"),
            render: (_, r) => (
              <Typography.Text copyable={{ text: r.url }} style={{ fontSize: fontSize.caption }}>
                {hostOf(r.url)}
              </Typography.Text>
            ),
          },
          {
            title: t("services.colStatus"),
            width: 130,
            render: (_, r) => {
              const m = metaOf(serviceStatusMap, r.status);
              return (
                <Space orientation="vertical" size={0}>
                  <HexTag color={m?.color}>{m ? t(m.labelKey) : r.status}</HexTag>
                  {(r.status === "running" || r.status === "unready") && (
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {r.ready ? t("services.ready") : t("services.notReady")}
                    </Typography.Text>
                  )}
                </Space>
              );
            },
          },
          {
            // 链到全局实例表按 uuid 前缀检索
            title: t("services.colInstance"),
            width: 120,
            render: (_, r) => {
              const inst = r.current_instance ?? r.rollout_instance;
              return inst ? (
                <Link to="/tenants" search={{ tab: "instances", iq: inst.uuid }}>
                  {inst.uuid.slice(0, 8)}
                </Link>
              ) : (
                "—"
              );
            },
          },
          { title: t("services.colRevision"), width: 70, render: (_, r) => `v${r.revision}` },
          {
            title: t("services.colNode"),
            dataIndex: "node_name",
            width: 160,
            render: (v: string | null) => v ?? "—",
          },
          { title: t("services.colCreatedAt"), dataIndex: "created_at", width: 170, render: formatDateTime },
          {
            title: t("services.colActions"),
            width: 110,
            render: (_, r) => {
              const inst = r.current_instance;
              const stoppable = inst != null && (r.status === "running" || r.status === "unready");
              return (
                <ReasonAction
                  label={t("tenants.forceStop")}
                  danger
                  title={t("services.forceStopTitle")}
                  confirmText={t("services.forceStopConfirm", { name: r.name, slug: r.slug })}
                  disabled={!writable || !stoppable}
                  disabledReason={!writable ? t("tenants.noPermission") : t("services.forceStopNeedsRunning")}
                  onSubmit={async (reason) => {
                    if (!inst) return;
                    await forceStop.mutateAsync({ uuid: inst.uuid, data: { reason } });
                    refresh();
                  }}
                />
              );
            },
          },
        ]}
      />
      {!compact && (
        <LoadMore
          hasNextPage={Boolean(hasNextPage)}
          loading={isFetchingNextPage}
          isError={isFetchNextPageError}
          loadedCount={rows.length}
          onLoadMore={() => void fetchNextPage()}
        />
      )}
    </>
  );
}
