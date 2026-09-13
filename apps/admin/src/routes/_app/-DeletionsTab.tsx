/** 注销申请 Tab:状态筛选入 URL(?dstatus=)+ 执行(L3,主动作)/ 驳回(ReasonAction,更多)。 */

import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, getRouteApi } from "@tanstack/react-router";
import { App, Button, Input, Select, Space, Table, Tag, Typography } from "antd";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { controlWidth, deletionStatusMap, fontSize, formatDateTime, layout, useNow, useUrlFilters } from "@superdl/ui";
import {
  EmptyState,
  FilterBar,
  GatedButton,
  RowActions,
  RowMoreMenu,
  TableErrorEmpty,
  TypeConfirmModal,
} from "@superdl/ui/components";
import { useApiErrorText, useFormat } from "@superdl/ui";

import { type DeletionRow, isApiError, useApproveDeletion, useDeletionRequests, useRejectDeletion } from "../../api";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { ReasonAction } from "../../components/ReasonAction";
import { StatusTag } from "@superdl/ui/components";
import { TenantLink } from "../../components/TenantLink";
import { REASON_MAX_LEN } from "../../lib/validators";
import { useAdminRole } from "../../stores/auth";

const routeApi = getRouteApi("/_app/tenants");

/** 注销申请:列表 + 处理。执行仅超管;校验计数全 0 且过冷静期才可点。 */
export function DeletionsTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { message } = App.useApp();
  const errText = useApiErrorText();
  const { formatMoney, formatCountdown } = useFormat();
  const role = useAdminRole();
  const isAdmin = role === "admin";
  const navigate = useNavigate({ from: "/tenants" });
  // 状态筛选入 URL(?dstatus=)
  const status = routeApi.useSearch({ select: (s) => s.dstatus });
  const commitFilters = useCallback(
    (patch: { dstatus?: string }) =>
      void navigate({ to: "/tenants", replace: true, search: (prev) => ({ ...prev, ...patch }) }),
    [navigate],
  );
  const filters = useUrlFilters({ search: { dstatus: status }, keys: ["dstatus"], commit: commitFilters });
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, error, refetch } = useDeletionRequests(status ? { status } : undefined);
  const rows: DeletionRow[] = data ?? [];
  const approve = useApproveDeletion();
  const reject = useRejectDeletion();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const [approving, setApproving] = useState<DeletionRow | null>(null);
  const [approveNote, setApproveNote] = useState("");
  const [approveLoading, setApproveLoading] = useState(false);
  // 冷静期倒计时 30s tick
  const nowTs = useNow(30_000);

  const closeApprove = () => {
    setApproving(null);
    setApproveNote("");
  };
  const runApprove = async () => {
    if (!approving) return;
    setApproveLoading(true);
    try {
      await approve.mutateAsync({ requestId: approving.id, data: { note: approveNote.trim() } });
      message.success(t("tenants.deletion.executed"));
      closeApprove();
    } catch (e) {
      // 校验不过 / 冷静期未满 → 409
      message.error(errText(e, t("common.actionFailed", { action: t("tenants.deletion.approveTitle") })));
    } finally {
      setApproveLoading(false);
      refresh();
    }
  };

  const cooldownLeft = (r: DeletionRow) => (r.status === "pending" ? formatCountdown(r.cooldown_ends_at) : null);
  const precheckClear = (r: DeletionRow) => r.instances_active === 0 && r.disks_active === 0 && Number(r.balance) === 0;
  const cooldownOver = (r: DeletionRow) => new Date(r.cooldown_ends_at).getTime() <= nowTs;

  return (
    <>
      <FilterBar hasFilter={filters.hasFilter} onClear={filters.clear}>
        <Select
          allowClear
          placeholder={t("tenants.deletion.statusFilter")}
          style={{ width: controlWidth.sm }}
          value={status}
          onChange={(v: string | undefined) => commitFilters({ dstatus: v })}
          options={Object.entries(deletionStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
      </FilterBar>
      <Table<DeletionRow>
        scroll={{ x: 1100 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="id"
        loading={isLoading}
        locale={{
          emptyText: isError ? (
            <TableErrorEmpty
              isError
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ) : (
            <EmptyState
              scene={filters.hasFilter ? "search" : "list"}
              compact
              secondaryAction={
                filters.hasFilter ? (
                  <Button size="small" onClick={filters.clear}>
                    {t("filter.clear", { ns: "shared" })}
                  </Button>
                ) : undefined
              }
            />
          ),
        }}
        dataSource={rows}
        columns={[
          { title: "ID", dataIndex: "id", width: 70, fixed: "left" },
          {
            title: t("tenants.deletion.colUser"),
            fixed: "left",
            width: 170,
            render: (_, r) => (
              <Space size={8}>
                <TenantLink id={r.user_id} />
                <span>{r.phone_masked}</span>
              </Space>
            ),
          },
          {
            title: t("tenants.deletion.colStatus"),
            dataIndex: "status",
            render: (v: string) => <StatusTag map={deletionStatusMap} value={v} variant="badge" />,
          },
          { title: t("tenants.deletion.colReason"), dataIndex: "reason", ellipsis: true },
          {
            title: t("tenants.deletion.colRequestedAt"),
            dataIndex: "requested_at",
            render: formatDateTime,
          },
          {
            title: t("tenants.deletion.colCooldownEnd"),
            dataIndex: "cooldown_ends_at",
            render: (v: string, r) => (
              <Space size={8}>
                <span>{formatDateTime(v)}</span>
                {r.status === "pending" && !cooldownOver(r) && <Tag color="orange">{cooldownLeft(r)}</Tag>}
              </Space>
            ),
          },
          {
            title: t("tenants.deletion.colPrecheck"),
            render: (_, r) => (
              <Space size={8}>
                <span>{t("tenants.deletion.precheckInstances", { count: r.instances_active })}</span>
                <span>{t("tenants.deletion.precheckDisks", { count: r.disks_active })}</span>
                <span>{formatMoney(r.balance)}</span>
              </Space>
            ),
          },
          {
            title: t("tenants.deletion.colProcessed"),
            render: (_, r) =>
              r.processed_at ? (
                <Space orientation="vertical" size={0}>
                  <Typography.Text style={{ fontSize: fontSize.caption }}>
                    {t("tenants.deletion.processedBy", {
                      id: r.processed_by ?? "-",
                      time: formatDateTime(r.processed_at),
                    })}
                  </Typography.Text>
                  {r.note && (
                    <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                      {r.note}
                    </Typography.Text>
                  )}
                </Space>
              ) : (
                "—"
              ),
          },
          {
            title: t("tenants.colActions"),
            fixed: "right",
            width: 150,
            render: (_, r) =>
              r.status === "pending" ? (
                <RowActions
                  primary={
                    <GatedButton
                      size="small"
                      danger
                      reason={isAdmin ? undefined : t("tenants.deletion.noPermission")}
                      onClick={() => setApproving(r)}
                    >
                      {t("tenants.deletion.approve")}
                    </GatedButton>
                  }
                  more={
                    <RowMoreMenu>
                      <ReasonAction
                        label={t("tenants.deletion.reject")}
                        type="text"
                        target={`#${r.user_id} · ${r.phone_masked}`}
                        title={t("tenants.deletion.rejectTitle")}
                        confirmText={t("tenants.deletion.rejectConfirm")}
                        disabled={!isAdmin}
                        disabledReason={t("tenants.deletion.noPermission")}
                        onSubmit={async (note) => {
                          await reject.mutateAsync({ requestId: r.id, data: { note } });
                          refresh();
                        }}
                      />
                    </RowMoreMenu>
                  }
                />
              ) : null,
          },
        ]}
      />
      <ListCapNote rows={rows.length} cap={LIST_CAPS.deletions} />

      {/* L3 确认:键入用户 ID + 必填操作原因;校验未过 / 冷静期未满时按钮保持禁用(ui-ux-spec §1 规则 7) */}
      {approving && (
        <TypeConfirmModal
          open
          title={t("tenants.deletion.approveTitle")}
          targetName={String(approving.user_id)}
          body={
            <Space orientation="vertical" size={8} style={{ width: "100%" }}>
              <Typography.Text strong>
                {t("tenants.deletion.approveTarget", {
                  id: approving.user_id,
                  phone: approving.phone_masked,
                })}
              </Typography.Text>
              <Typography.Text>
                {t("tenants.deletion.approveCheckLine", {
                  instances: approving.instances_active,
                  disks: approving.disks_active,
                  balance: formatMoney(approving.balance),
                })}
              </Typography.Text>
              {!precheckClear(approving) && (
                <Typography.Text type="danger">{t("tenants.deletion.approveBlocked")}</Typography.Text>
              )}
              {!cooldownOver(approving) && (
                <Typography.Text type="warning">
                  {t("tenants.deletion.cooldownRemaining", {
                    countdown: cooldownLeft(approving) ?? "",
                  })}
                </Typography.Text>
              )}
              <Typography.Text type="secondary">{t("tenants.deletion.approveConfirmText")}</Typography.Text>
              <Input.TextArea
                rows={2}
                maxLength={REASON_MAX_LEN}
                showCount
                value={approveNote}
                onChange={(e) => setApproveNote(e.target.value)}
                placeholder={t("tenants.deletion.approveNotePlaceholder")}
                aria-label={t("tenants.deletion.approveNotePlaceholder")}
              />
            </Space>
          }
          checkboxLabel={t("tenants.deletion.approveAck")}
          confirmLabel={t("tenants.deletion.approve")}
          cancelLabel={t("common.cancel", { ns: "shared" })}
          loading={approveLoading}
          extraDisabled={!precheckClear(approving) || !cooldownOver(approving) || approveNote.trim().length < 2}
          onConfirm={() => void runApprove()}
          onCancel={closeApprove}
        />
      )}
    </>
  );
}
