/** SKU 与定价:FilterBar(型号 / 档位 / 在售 / 名称,入 URL,客户端过滤)+ 列表(状态列在售 / 已下架;操作固定右:编辑 + 更多 ▾ 上架 / 下架,改价确认带影响面);新建 / 编辑抽屉在 -SkuDrawerForm,表单常量与联动纯函数在 -skuForm。 */

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { App, Button, Card, Input, Select, Table, Tag } from "antd";
import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { controlWidth, layout, skuTierMap, skuVariant, type SkuVariant } from "@superdl/ui";
import {
  EmptyState,
  FilterBar,
  GatedButton,
  PageContainer,
  RowActions,
  RowMoreMenu,
  TableErrorEmpty,
  useConfirm,
} from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";
import { useApiErrorText } from "@superdl/ui";
import { useUrlCommittedInput, useUrlFilters } from "@superdl/ui";

import { StatusTag } from "@superdl/ui/components";
import { type SkuAdminOut, isApiError, useAdminSkus, useUpdateSku } from "../../api";
import { ReasonAction } from "../../components/ReasonAction";
import { canWriteOps, useAdminRole } from "../../stores/auth";
import { SkuDrawerForm } from "./-SkuDrawerForm";

export interface SkusSearch {
  /** 卡型(精确匹配 gpu_model) */
  model?: string;
  /** 档位(skuTierMap 键) */
  tier?: SkuVariant;
  /** 在售筛选:on = 在售,off = 已下架,缺省 = 全部 */
  sale?: "on" | "off";
  /** 名称检索(子串,大小写不敏感) */
  q?: string;
}

/** 全部筛选项客户端生效(SKU 一次取全量),白名单外与空值一律剥离。 */
export function skusValidateSearch(search: Record<string, unknown>): SkusSearch {
  const out: SkusSearch = {};
  if (typeof search.model === "string" && search.model.trim()) out.model = search.model;
  if (typeof search.tier === "string" && search.tier in skuTierMap) out.tier = search.tier as SkuVariant;
  if (search.sale === "on" || search.sale === "off") out.sale = search.sale;
  if (typeof search.q === "string" && search.q.trim()) out.q = search.q;
  return out;
}

export const Route = createFileRoute("/_app/skus")({
  validateSearch: skusValidateSearch,
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
  // 筛选入 URL;SKU 一次取全量,过滤在客户端
  const navigate = useNavigate({ from: "/skus" });
  const { model, tier, sale, q } = Route.useSearch();
  const setUrl = useCallback(
    (patch: Partial<SkusSearch>) =>
      void navigate({ to: "/skus", replace: true, search: (prev) => ({ ...prev, ...patch }) }),
    [navigate],
  );
  const commitQ = useCallback((next: string | undefined) => setUrl({ q: next }), [setUrl]);
  const { value: qInput, setValue: setQInput } = useUrlCommittedInput(q, commitQ);
  const filters = useUrlFilters({
    search: { model, tier, sale, q },
    keys: ["model", "tier", "sale", "q"],
    commit: setUrl,
  });
  // 型号选项取当前列表的去重值(没有单独的型号字典端点)
  const modelOptions = useMemo(
    () => [...new Set((skus ?? []).map((s) => s.gpu_model).filter(Boolean))].sort((a, b) => a.localeCompare(b)),
    [skus],
  );
  const rows = useMemo(() => {
    const needle = q?.trim().toLowerCase();
    return (skus ?? []).filter((r) => {
      if (model && r.gpu_model !== model) return false;
      if (tier && skuVariant(r.tier, r.pool_label) !== tier) return false;
      if (sale && (r.status === "on") !== (sale === "on")) return false;
      if (needle && !r.name.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [skus, model, tier, sale, q]);

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
        <FilterBar hasFilter={filters.hasFilter} onClear={filters.clear} count={rows.length}>
          <Select
            allowClear
            showSearch
            placeholder={t("skus.filterModel")}
            style={{ width: controlWidth.md }}
            value={model}
            onChange={(v: string | undefined) => setUrl({ model: v })}
            options={modelOptions.map((m) => ({ value: m, label: m }))}
          />
          <Select
            allowClear
            placeholder={t("skus.colTier")}
            style={{ width: controlWidth.md }}
            value={tier}
            onChange={(v: SkuVariant | undefined) => setUrl({ tier: v })}
            options={Object.entries(skuTierMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
          />
          <Select
            allowClear
            placeholder={t("skus.colStatus")}
            style={{ width: controlWidth.sm }}
            value={sale}
            onChange={(v: "on" | "off" | undefined) => setUrl({ sale: v })}
            options={[
              { value: "on", label: t("skus.statusOnSale") },
              { value: "off", label: t("skus.statusOffShelf") },
            ]}
          />
          <Input.Search
            allowClear
            placeholder={t("skus.searchPlaceholder")}
            style={{ width: controlWidth.md }}
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            onSearch={(v) => commitQ(v.trim() || undefined)}
          />
        </FilterBar>
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
                        // 上架是恢复方向:只填原因,不做第二步确认;规格缺要素被拒时给「强制上架」出口
                        <ReasonAction
                          label={t("skus.onSale")}
                          type="text"
                          target={r.name}
                          confirm={false}
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
