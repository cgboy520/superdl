/** 全局在线服务表(在线服务页与租户抽屉共用);唯一处置「强制停止」委托当前版本实例的 force-stop。 */

import { flattenPages, fontSize, formatDateTime, layout, metaOf, serviceStatusMap } from "@superdl/ui";
import { CopyField, CursorTable, EmptyState, HexTag, Mono, RowActions, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Button, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
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
  hasFilter,
  onClearFilters,
}: {
  userId?: number;
  q?: string;
  includeReleased?: boolean;
  /** 抽屉内:小表 + 前 100 条明示截断,不出归属列 */
  compact?: boolean;
  /** 页面筛选态非空(空态切到「无匹配」) */
  hasFilter?: boolean;
  /** 空态「清除筛选」 */
  onClearFilters?: () => void;
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
  const { data, queryKey, isLoading, isError, error, refetch } = servicesQ;
  const forbidden = isApiError(error) && error.status === 403;
  const rows = flattenPages(data);
  const total = data?.pages[0]?.total ?? null;
  const forceStop = useForceStop();
  const refresh = () => void qc.invalidateQueries({ queryKey });

  const columns: TableColumnsType<AdminServiceOut> = [
    {
      title: t("services.colService"),
      fixed: compact ? undefined : "left",
      width: 200,
      render: (_, r) => (
        <Space orientation="vertical" size={0}>
          <span>{r.name}</span>
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            <Mono>{r.slug}</Mono>
          </Typography.Text>
        </Space>
      ),
    },
    ...(compact ? [] : [tenantColumn<AdminServiceOut>(t("tenants.colOwner"), 90)]),
    {
      title: t("services.colEndpoint"),
      render: (_, r) => <CopyField value={r.url} display={hostOf(r.url)} />,
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
      title: t("services.colInstance"),
      width: 120,
      render: (_, r) => {
        const inst = r.current_instance ?? r.rollout_instance;
        return inst ? (
          <Link to="/tenants" search={{ tab: "instances", iq: inst.uuid }}>
            <Mono truncate={8}>{inst.uuid}</Mono>
          </Link>
        ) : (
          "—"
        );
      },
    },
    { title: t("services.colRevision"), width: 70, align: "right", render: (_, r) => `v${r.revision}` },
    {
      title: t("services.colNode"),
      dataIndex: "node_name",
      width: 160,
      render: (v: string | null) => (v ? <Mono>{v}</Mono> : "—"),
    },
    { title: t("services.colCreatedAt"), dataIndex: "created_at", width: 170, render: formatDateTime },
    {
      title: t("services.colActions"),
      width: 110,
      fixed: compact ? undefined : "right",
      render: (_, r) => {
        const inst = r.current_instance;
        const stoppable = inst != null && (r.status === "running" || r.status === "unready");
        return (
          <RowActions
            primary={
              <ReasonAction
                label={t("tenants.forceStop")}
                target={`${r.name} · ${r.slug}`}
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
            }
          />
        );
      },
    },
  ];

  return (
    <>
      {compact && total !== null && total > rows.length && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 8, fontSize: fontSize.caption }}>
          {t("services.capped", { shown: rows.length, total })}{" "}
          <Link to="/services" search={{ user_id: userId }}>
            {t("services.viewAll")}
          </Link>
        </Typography.Text>
      )}
      {compact ? (
        <Table<AdminServiceOut>
          size="small"
          rowKey="slug"
          loading={isLoading}
          pagination={false}
          scroll={{ x: 900, y: 420 }}
          locale={{
            emptyText:
              isError || forbidden ? (
                <TableErrorEmpty isError={isError} isForbidden={forbidden} onRetry={() => void refetch()} compact />
              ) : (
                <EmptyState scene="list" compact />
              ),
          }}
          dataSource={rows}
          columns={columns}
        />
      ) : (
        <CursorTable<AdminServiceOut>
          query={servicesQ}
          rows={rows}
          emptyNode={
            <EmptyState
              scene={hasFilter ? "search" : "list"}
              compact
              secondaryAction={
                hasFilter && onClearFilters ? (
                  <Button size="small" onClick={onClearFilters}>
                    {t("filter.clear", { ns: "shared" })}
                  </Button>
                ) : undefined
              }
            />
          }
          rowKey="slug"
          scroll={{ x: 1240 }}
          sticky={{ offsetHeader: layout.topBarHeight }}
          columns={columns}
        />
      )}
    </>
  );
}
