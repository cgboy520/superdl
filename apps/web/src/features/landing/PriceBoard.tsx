/** 首屏实时行情板:按 (型号 × 档位) 取代表规格,直接把「价格 + 可开台数 + 租用入口」摆在第一屏(CTA 即库存)。
 *  数字全部取 /skus 公开端点并显示更新时刻;整块查询失败降级为「前往算力市场」,不渲染假数字。
 *  面板是墨色深底:内部套一层 dark 算法的 ConfigProvider,让 antd 子件(文字 / 骨架 / 按钮)自己按深底取色,不逐处写颜色。 */

import type { SkuMarketOut } from "@superdl/api-client";
import {
  brand,
  compareAmounts,
  fontFamilyMono,
  fontSize,
  fontWeight,
  inkPanelTokens,
  layout,
  POLL,
  skuTierMap,
  skuVariant,
  space,
  textOnAccent,
  themeColors,
  useFormat,
} from "@superdl/ui";
import { Freshness, StatusTag } from "@superdl/ui/components";
import { Link } from "@tanstack/react-router";
import { Button, ConfigProvider, Skeleton, theme, Typography } from "antd";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useSkus } from "../../api/queries";

/** 面板上的一行:同型号同档位取最低价;有货的优先于无货的。 */
interface BoardRow {
  key: string;
  sku: SkuMarketOut;
  /** 非空型号(CPU 规格已在入口滤掉) */
  model: string;
  variant: string;
  available: number;
}

const ROW_LIMIT = 5;

function pickRows(skus: readonly SkuMarketOut[]): BoardRow[] {
  const best = new Map<string, BoardRow>();
  for (const sku of skus) {
    if (!sku.gpu_model) continue; // GPU 行情板不摆 CPU 机型(市场页有)
    const variant = skuVariant(sku.tier, sku.pool_label);
    const key = `${sku.gpu_model}:${variant}`;
    const available = sku.available_count ?? 0;
    const cur = best.get(key);
    // 同组内:有货优先,其次单价低者
    const better =
      cur === undefined ||
      (available > 0 && cur.available === 0) ||
      (available > 0 === cur.available > 0 && compareAmounts(sku.price_hourly, cur.sku.price_hourly) < 0);
    if (better) best.set(key, { key, sku, model: sku.gpu_model, variant, available });
  }
  return [...best.values()]
    .sort((a, b) => {
      // 有货在前;组内按单价从低到高(便宜的更像入口)
      if (a.available > 0 !== b.available > 0) return a.available > 0 ? -1 : 1;
      return compareAmounts(a.sku.price_hourly, b.sku.price_hourly);
    })
    .slice(0, ROW_LIMIT);
}

function BoardRowView({ row }: { row: BoardRow }) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatHourlyPrice } = useFormat();
  const { sku, model, variant, available } = row;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: space.md,
        padding: `${space.md}px 0`,
        borderTop: `1px solid ${brand.inkDivider}`,
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: space.sm, minWidth: 0, flex: "1 1 168px" }}>
        <span
          style={{
            fontFamily: fontFamilyMono,
            fontSize: fontSize.sectionTitle,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {model}
        </span>
        <StatusTag map={skuTierMap} value={variant} hint={false} />
      </div>
      <div style={{ minWidth: 132, flex: "0 0 auto" }}>
        <span style={{ fontFamily: fontFamilyMono, fontSize: fontSize.pageTitle, fontWeight: fontWeight.semibold }}>
          {formatHourlyPrice(sku.price_hourly)}
        </span>
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption, marginInlineStart: 6 }}>
          {t("landing.board.perCard")}
        </Typography.Text>
      </div>
      <div style={{ minWidth: 116, flex: "1 1 116px" }}>
        {available > 0 ? (
          <span
            style={{ fontFamily: fontFamilyMono, color: themeColors["web-dark"].positive, fontSize: fontSize.body }}
          >
            {t("copy.stockAvailable", { count: available })}
          </span>
        ) : (
          <span style={{ display: "inline-flex", gap: space.sm, alignItems: "baseline", flexWrap: "wrap" }}>
            <Typography.Text type="secondary">{t("copy.outOfStock")}</Typography.Text>
            {/* 售罄不是死胡同:给同型号其它档位的入口 */}
            <Link to="/market" search={{ model }} style={{ fontSize: fontSize.caption }}>
              {t("landing.board.otherTiers")}
            </Link>
          </span>
        )}
      </div>
      {/* 售罄行不摆死按钮:同行的「看其它档位」就是出路 */}
      {available > 0 && (
        <Link to="/market" search={{ model, sku: sku.id }}>
          <Button size="small">{t("landing.board.rent")}</Button>
        </Link>
      )}
    </div>
  );
}

export function PriceBoard({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation(["web", "shared"]);
  const skusQ = useSkus({ refetchInterval: POLL.publicBoard });
  const rows = useMemo(() => pickRows(skusQ.data ?? []), [skusQ.data]);

  if (skusQ.isError) {
    // 行情取不到就不摆空面板,直接给市场入口
    return (
      <Link to="/market">
        <Button size="large" ghost>
          {t("landing.pricing.fallbackCta")}
        </Button>
      </Link>
    );
  }
  return (
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm, token: inkPanelTokens }}>
      <section
        aria-label={t("landing.board.title")}
        style={{
          background: brand.ink,
          border: `1px solid ${brand.inkBorder}`,
          borderRadius: layout.cardRadius,
          padding: `${space.lg}px ${space.xl}px`,
          width: compact ? "100%" : 520,
          maxWidth: "100%",
          color: textOnAccent,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: space.sm,
            flexWrap: "wrap",
            paddingBottom: space.sm,
          }}
        >
          <Typography.Text style={{ fontWeight: fontWeight.semibold }}>{t("landing.board.title")}</Typography.Text>
          {/* 面板自身不显示轮询周期,只给「更新于」 */}
          <Freshness updatedAt={skusQ.dataUpdatedAt} intervalMs={false} />
        </div>
        {skusQ.isLoading
          ? [0, 1, 2, 3, 4].map((i) => (
              <div key={i} style={{ padding: `${space.md}px 0`, borderTop: `1px solid ${brand.inkDivider}` }}>
                <Skeleton active title={{ width: "60%" }} paragraph={false} />
              </div>
            ))
          : rows.map((row) => <BoardRowView key={row.key} row={row} />)}
        <div style={{ paddingTop: space.md, borderTop: `1px solid ${brand.inkDivider}` }}>
          <Link to="/market" style={{ color: brand.indigo50 }}>
            {t("landing.board.allSpecs")}
          </Link>
        </div>
      </section>
    </ConfigProvider>
  );
}
