/** 结算缺口:重放补结 / 人工核销入口(告警 superdl_settlement_gap_unresolved)。 */

import { formatDateTime } from "@superdl/ui";
import { LoadMore, TableErrorEmpty } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { App, Button, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminSettlementGapOut } from "@superdl/api-client";

import {
  isApiError,
  useReplaySettlementGap,
  useResolveSettlementGap,
  useSettlementGaps,
} from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { useApiErrorText } from "@superdl/ui";
import { canWriteFinance, useAdminRole } from "../../stores/auth";

const REASON_LABEL_KEY = {
  catchup_truncated: "finance.gapReasonCatchupTruncated",
  dead_letter: "finance.gapReasonDeadLetter",
  watermark_missing: "finance.gapReasonWatermarkMissing",
  grace_overlap: "finance.gapReasonGraceOverlap",
} as const;
type GapReason = keyof typeof REASON_LABEL_KEY;

// 缺口类型 → 文案键
const KIND_LABEL_KEY = {
  hourly: "finance.gapKindHourly",
  daily_disk: "finance.gapKindDailyDisk",
} as const;
type GapKind = keyof typeof KIND_LABEL_KEY;

export function SettlementGapsTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteFinance(role);

  const [kind, setKind] = useState<GapKind | undefined>(undefined);
  const [unresolvedOnly, setUnresolvedOnly] = useState(true);
  const params = {
    ...(kind ? { kind } : {}),
    unresolved: unresolvedOnly,
  };
  const { data, queryKey, isLoading, isError, error, refetch, hasNextPage, fetchNextPage, isFetchingNextPage, isFetchNextPageError } =
    useSettlementGaps(params);
  const refresh = () => qc.invalidateQueries({ queryKey });

  const replay = useReplaySettlementGap();
  const resolve = useResolveSettlementGap();

  const items: AdminSettlementGapOut[] = (data?.pages ?? []).flatMap((p) => p.items);

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          allowClear
          placeholder={t("finance.gapKind")}
          style={{ width: 160 }}
          value={kind}
          onChange={(v) => setKind(v)}
          options={(Object.keys(KIND_LABEL_KEY) as GapKind[]).map((k) => ({
            value: k,
            label: t(KIND_LABEL_KEY[k]),
          }))}
        />
        <Space size={6}>
          <Switch checked={unresolvedOnly} onChange={setUnresolvedOnly} />
          <Typography.Text type="secondary">{t("finance.gapUnresolvedOnly")}</Typography.Text>
        </Space>
        {/* 手动刷新重置回第一页 */}
        <Button onClick={() => void qc.resetQueries({ queryKey })}>{t("common.refresh")}</Button>
      </Space>
      <Table<AdminSettlementGapOut>
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={items}
        pagination={false}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        columns={[
          { title: "ID", dataIndex: "id", width: 80 },
          {
            title: t("finance.gapKind"),
            dataIndex: "kind",
            width: 110,
            render: (v: string) => {
              const labelKey = v in KIND_LABEL_KEY ? KIND_LABEL_KEY[v as GapKind] : undefined;
              return labelKey ? t(labelKey) : v;
            },
          },
          {
            title: t("finance.gapWindow"),
            dataIndex: "window_start",
            render: (v: string) => formatDateTime(v),
          },
          {
            title: t("finance.gapObject"),
            dataIndex: "object_id",
            width: 110,
            render: (v: number) => (v === 0 ? <Tag>{t("finance.gapWholeWindow")}</Tag> : `#${v}`),
          },
          {
            title: t("finance.gapReason"),
            dataIndex: "reason",
            render: (v: string) => {
              const labelKey =
                v in REASON_LABEL_KEY ? REASON_LABEL_KEY[v as GapReason] : undefined;
              return (
                <Tag color={v === "dead_letter" ? "red" : "orange"}>
                  {labelKey ? t(labelKey) : v}
                </Tag>
              );
            },
          },
          {
            title: t("finance.gapCreatedAt"),
            dataIndex: "created_at",
            render: (v: string) => formatDateTime(v),
          },
          {
            title: t("finance.gapResolvedAt"),
            dataIndex: "resolved_at",
            render: (v: string | null) =>
              v ? formatDateTime(v) : <Typography.Text type="secondary">—</Typography.Text>,
          },
          {
            title: t("finance.gapActions"),
            key: "actions",
            width: 180,
            render: (_, row) =>
              row.resolved_at ? null : (
                <Space size={4}>
                  {/* 重放端点无 reason 负载,用 Popconfirm */}
                  <Popconfirm
                    title={t("finance.gapReplayConfirm")}
                    onConfirm={async () => {
                      try {
                        await replay.mutateAsync({ gapId: row.id });
                        message.success(t("finance.gapReplayed"));
                        refresh();
                      } catch (e) {
                        message.error(errText(e, t("common.actionFailed", { action: t("finance.gapReplay") })));
                      }
                    }}
                  >
                    <Button type="link" size="small" disabled={!writable} style={{ whiteSpace: "nowrap" }}>
                      {t("finance.gapReplay")}
                    </Button>
                  </Popconfirm>
                  <ReasonAction
                    label={t("finance.gapResolve")}
                    title={t("finance.gapResolveTitle")}
                    confirmText={t("finance.gapResolveConfirm")}
                    disabled={!writable}
                    disabledReason={t("finance.financeOnlyGap")}
                    onSubmit={async (reason) => {
                      await resolve.mutateAsync({ gapId: row.id, data: { note: reason } });
                      refresh();
                      return t("finance.gapResolved");
                    }}
                  />
                </Space>
              ),
          },
        ]}
      />
      <LoadMore
        hasNextPage={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={items.length}
        onLoadMore={() => void fetchNextPage()}
      />
    </>
  );
}
