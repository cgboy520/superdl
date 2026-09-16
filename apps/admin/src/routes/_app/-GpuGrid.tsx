/** Node GPU heat grid: per-card utilisation / VRAM / temperature tiles and legend. */

import { Space, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, heatColors, space, textOnAccent } from "@superdl/ui";

import { type NodeMetricsOut, type NodeRow } from "../../api";

export const HEAT_COLORS = { idle: adminColors.gridLine, ...heatColors };

export const HEAT_LEGEND_KEY = {
  idle: "nodes.heatLegend.idle",
  low: "nodes.heatLegend.low",
  mid: "nodes.heatLegend.mid",
  high: "nodes.heatLegend.high",
} as const;

export function heatColor(util: number): string {
  if (util < 10) return HEAT_COLORS.idle;
  if (util < 60) return HEAT_COLORS.low;
  if (util < 85) return HEAT_COLORS.mid;
  return HEAT_COLORS.high;
}

export function last(points?: [number, number][] | null): number | null {
  const p = points?.[points.length - 1];
  return p ? p[1] : null;
}

/** Per-card tile: coloured by util when metrics exist (tooltip gives util/VRAM/temperature); without a source it falls back to the two states "rented / idle", idle tiles hatched. */
export function GpuGrid({ node, metrics }: { node: NodeRow; metrics: NodeMetricsOut | undefined }) {
  const { t } = useTranslation();
  const byIndex = new Map((metrics?.gpus ?? []).map((g) => [g.index, g]));
  const live = Boolean(metrics?.available && byIndex.size > 0);
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {Array.from({ length: node.gpu_total }, (_, i) => {
          const g = byIndex.get(String(i));
          const util = live ? last(g?.util) : null;
          const mem = last(g?.mem_used_mb);
          const temp = last(g?.temp);
          const used = i < node.gpu_used;
          const title = live
            ? t("nodes.gpuCellLive", {
                index: i,
                util: util == null ? "—" : Math.round(util),
                mem: mem == null ? "—" : Math.round(mem / 1024),
                temp: temp == null ? "—" : Math.round(temp),
              })
            : used
              ? t("nodes.gpuCellUsed", { index: i })
              : t("nodes.gpuCellFree", { index: i });
          const bg = live
            ? heatColor(util ?? 0)
            : used
              ? adminColors.dataAccent
              : `repeating-linear-gradient(135deg, ${adminColors.gridLine} 0 6px, transparent 6px 12px)`;
          return (
            <Tooltip key={i} title={title}>
              <div
                role="img"
                aria-label={title}
                style={{
                  width: 52,
                  height: 44,
                  borderRadius: 6,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: fontSize.caption,
                  lineHeight: 1.2,
                  background: bg,
                  color: !live
                    ? used
                      ? adminColors.bgBase
                      : adminColors.textSecondary
                    : (util ?? 0) >= 10
                      ? textOnAccent
                      : adminColors.textSecondary,
                  fontWeight: 600,
                }}
              >
                <span>{i}</span>
                {live && <span>{util == null ? "—" : `${Math.round(util)}%`}</span>}
              </div>
            </Tooltip>
          );
        })}
      </div>
      <Space size={space.md} wrap style={{ marginTop: 12 }}>
        {(Object.keys(HEAT_LEGEND_KEY) as (keyof typeof HEAT_LEGEND_KEY)[]).map((key) => (
          <Space key={key} size={space.xs}>
            <span
              style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: HEAT_COLORS[key] }}
            />
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t(HEAT_LEGEND_KEY[key])}
            </Typography.Text>
          </Space>
        ))}
        <Space size={space.xs}>
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 12,
              borderRadius: 3,
              background: `repeating-linear-gradient(135deg, ${adminColors.gridLine} 0 4px, transparent 4px 8px)`,
              border: `1px solid ${adminColors.gridLine}`,
            }}
          />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("nodes.heatLegend.offlineFree")}
          </Typography.Text>
        </Space>
        <Space size={space.xs}>
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 12,
              borderRadius: 3,
              background: adminColors.dataAccent,
            }}
          />
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("nodes.heatLegend.offlineUsed")}
          </Typography.Text>
        </Space>
      </Space>
    </>
  );
}
