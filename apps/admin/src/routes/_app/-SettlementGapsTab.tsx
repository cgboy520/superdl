/** 结算缺口:水位线被越过但账未结清的窗口留痕(重放补结 / 人工核销闭环)。
 * 未核销缺口由 DB 口径告警 superdl_settlement_gap_unresolved 持续曝光,本页是处理入口。
 */

import { formatDateTime } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { App, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminSettlementGapOut } from "@superdl/api-client";

import {
  useReplaySettlementGap,
  useResolveSettlementGap,
  useSettlementGaps,
} from "../../api";
import { LoadMoreButton } from "../../components/LoadMore";
import { ReasonAction } from "../../components/ReasonAction";
import { useApiErrorText } from "../../lib/apiError";
import { canWriteFinance, useAdminRole } from "../../stores/auth";

const REASON_LABEL_KEY = {
  catchup_truncated: "finance.gapReasonCatchupTruncated",
  dead_letter: "finance.gapReasonDeadLetter",
  watermark_missing: "finance.gapReasonWatermarkMissing",
  grace_overlap: "finance.gapReasonGraceOverlap",
} as const;
type GapReason = keyof typeof REASON_LABEL_KEY;

export function SettlementGapsTab() {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteFinance(role);

  const [kind, setKind] = useState<string | undefined>(undefined);
  const [unresolvedOnly, setUnresolvedOnly] = useState(true);
  const params = {
    ...(kind ? { kind: kind as "hourly" | "daily_disk" } : {}),
    unresolved: unresolvedOnly,
  };
  const { data, queryKey, isLoading, isError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } =
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
          options={[
            { value: "hourly", label: "hourly" },
            { value: "daily_disk", label: "daily_disk" },
          ]}
        />
        <Space size={6}>
          <Switch checked={unresolvedOnly} onChange={setUnresolvedOnly} />
          <Typography.Text type="secondary">{t("finance.gapUnresolvedOnly")}</Typography.Text>
        </Space>
      </Space>
      <Table<AdminSettlementGapOut>
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={items}
        pagination={false}
        locale={{
          emptyText: isError ? (
            <Typography.Link onClick={() => void refetch()}>{t("common.retry")}</Typography.Link>
          ) : undefined,
        }}
        columns={[
          { title: "ID", dataIndex: "id", width: 80 },
          { title: t("finance.gapKind"), dataIndex: "kind", width: 110 },
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
                    <Typography.Link disabled={!writable} style={{ whiteSpace: "nowrap" }}>
                      {t("finance.gapReplay")}
                    </Typography.Link>
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
      <LoadMoreButton
        visible={!!hasNextPage}
        loading={isFetchingNextPage}
        onClick={() => void fetchNextPage()}
      />
    </>
  );
}
