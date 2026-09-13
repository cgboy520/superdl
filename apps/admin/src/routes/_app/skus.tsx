/** SKU 与定价:列表(状态列在售 / 已下架;操作固定右:编辑 + 更多 ▾ 上架 / 下架,改价确认带影响面);新建 / 编辑抽屉在 -SkuDrawerForm,表单常量与联动纯函数在 -skuForm。 */

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { App, Card, Table, Tag } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { layout, skuTierMap, skuVariant } from "@superdl/ui";
import {
  EmptyState,
  GatedButton,
  PageContainer,
  RowActions,
  RowMoreMenu,
  TableErrorEmpty,
  useConfirm,
} from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";

import { StatusTag } from "../../components/StatusTag";
import { type SkuAdminOut, isApiError, useAdminSkus, useUpdateSku } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { SkuDrawerForm } from "./-SkuDrawerForm";

export const Route = createFileRoute("/_app/skus")({
  component: SkusPage,
});

function SkusPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { formatHourlyPrice } = useFormat();
  const { message } = App.useApp();
  const confirm = useConfirm();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const qc = useQueryClient();
  const { data: skus, queryKey, isLoading, isError, error, refetch } = useAdminSkus();
  const [editing, setEditing] = useState<SkuAdminOut | "new" | null>(null);

  const refresh = () => void qc.invalidateQueries({ queryKey });
  // 行级上下架共一个变更实例;成功只刷新,文案由 ReasonAction / 强制上架确认各自承担
  const skuRowUpdate = useUpdateSku();

  return (
    <PageContainer
      width="full"
      title={t("menu.skus")}
      extra={
        <GatedButton
          type="primary"
          reason={writable ? undefined : t("common.readonlyNoCreate")}
          onClick={() => setEditing("new")}
        >
          {t("skus.newSku")}
        </GatedButton>
      }
    >
      <Card>
        <Table<SkuAdminOut>
          scroll={{ x: 1440 }}
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
              <EmptyState scene="list" compact />
            ),
          }}
          dataSource={skus ?? []}
          pagination={false}
          columns={[
            { title: t("skus.colName"), dataIndex: "name", fixed: "left", width: 200 },
            { title: t("skus.colGpuModel"), dataIndex: "gpu_model" },
            {
              title: t("skus.colTier"),
              render: (_, r) => {
                const v = skuVariant(r.tier, r.pool_label);
                return <StatusTag map={skuTierMap} value={v} />;
              },
            },
            {
              title: t("skus.colSlice"),
              render: (_, r) =>
                r.tier === "cpu"
                  ? "—"
                  : r.pool_label === "mig"
                    ? r.mig_profile
                    : t("skus.sliceShared", { pct: r.gpu_cores_pct, vram: r.vram_gb }),
            },
            {
              // 容量 = 匹配型号×池的物理卡数;CPU 规格不带卡
              title: t("skus.colCapacity"),
              dataIndex: "capacity_gpus",
              align: "right",
              render: (v: number, r) =>
                r.tier === "cpu" ? "—" : v === 0 && r.status === "on" ? <Tag color="red">0</Tag> : v,
            },
            {
              title: t("skus.colSoldShare"),
              dataIndex: "sold_share",
              align: "right",
              render: (v: string | null) => (v == null ? "—" : `${Math.round(Number(v) * 100)}%`),
            },
            {
              title: t("skus.colActualOversell"),
              align: "right",
              render: (_, r) => {
                if (r.actual_oversell == null) return "—";
                const over = Number(r.actual_oversell) >= Number(r.oversell_cores);
                return over ? <Tag color="red">{r.actual_oversell}×</Tag> : `${r.actual_oversell}×`;
              },
            },
            {
              title: t("skus.colOversellCores"),
              dataIndex: "oversell_cores",
              align: "right",
              render: (v: string) => `${v}×`,
            },
            {
              title: t("skus.colPrice"),
              dataIndex: "price_hourly",
              align: "right",
              render: (v: string) => formatHourlyPrice(v),
            },
            {
              title: t("skus.colPeriod"),
              dataIndex: "period_enabled",
              width: 100,
              render: (v: boolean) =>
                v ? <Tag color="blue">{t("skus.periodOn")}</Tag> : <Tag>{t("skus.periodOff")}</Tag>,
            },
            {
              title: t("skus.colSpot"),
              dataIndex: "spot_enabled",
              width: 100,
              render: (v: boolean) =>
                v ? <Tag color="orange">{t("skus.spotOn")}</Tag> : <Tag>{t("skus.spotOff")}</Tag>,
            },
            {
              title: t("skus.colStatus"),
              dataIndex: "status",
              width: 100,
              render: (v: string) =>
                v === "on" ? <Tag color="green">{t("skus.statusOnSale")}</Tag> : <Tag>{t("skus.statusOffShelf")}</Tag>,
            },
            {
              title: t("skus.colActions"),
              fixed: "right",
              width: 140,
              render: (_, r) => (
                <RowActions
                  primary={
                    <GatedButton
                      size="small"
                      reason={writable ? undefined : t("common.readonlyNoEdit")}
                      onClick={() => setEditing(r)}
                    >
                      {t("skus.edit")}
                    </GatedButton>
                  }
                  more={
                    <RowMoreMenu>
                      {r.status === "on" ? (
                        <ReasonAction
                          label={t("skus.offSale")}
                          type="text"
                          target={r.name}
                          danger
                          title={t("skus.offSaleTitle")}
                          confirmText={t("skus.offSaleConfirm", { name: r.name })}
                          disabled={!writable}
                          disabledReason={t("nodes.readonlyNoOp")}
                          onSubmit={async (reason) => {
                            await skuRowUpdate.mutateAsync(
                              { skuId: r.id, data: { status: "off", reason } },
                              { onSuccess: refresh },
                            );
                          }}
                        />
                      ) : (
                        // 上架:规格缺要素被拒时给「强制上架」出口
                        <ReasonAction
                          label={t("skus.onSale")}
                          type="text"
                          target={r.name}
                          title={t("skus.onSaleTitle")}
                          confirmText={t("skus.onSaleConfirm", { name: r.name })}
                          disabled={!writable}
                          disabledReason={t("nodes.readonlyNoOp")}
                          onSubmit={async (reason) => {
                            try {
                              await skuRowUpdate.mutateAsync(
                                { skuId: r.id, data: { status: "on", reason } },
                                { onSuccess: refresh },
                              );
                            } catch (e) {
                              // SKU_NOT_SELLABLE:确认后带 force 重放;其他错误继续抛给 ReasonAction
                              if (isApiError(e) && e.code === "SKU_NOT_SELLABLE") {
                                confirm({
                                  title: t("skus.notSellableTitle"),
                                  consequences: [errText(e, t("skus.toggleFailed"))],
                                  okText: t("skus.forceOn"),
                                  danger: true,
                                  onOk: () => {
                                    skuRowUpdate.mutate(
                                      {
                                        skuId: r.id,
                                        data: { status: "on", reason },
                                        force: true,
                                      },
                                      {
                                        onSuccess: refresh,
                                        onError: (err) => {
                                          message.error(errText(err, t("skus.toggleFailed")));
                                        },
                                      },
                                    );
                                  },
                                });
                              }
                              throw e;
                            }
                          }}
                        />
                      )}
                    </RowMoreMenu>
                  }
                />
              ),
            },
          ]}
        />
        <SkuDrawerForm editing={editing} onClose={() => setEditing(null)} onSaved={refresh} />
      </Card>
    </PageContainer>
  );
}
