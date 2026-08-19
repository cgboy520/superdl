/**
 * GPU 利用率迷你 sparkline(实例列表列,近 1h)。自绘 SVG —— 每行一个 ECharts 实例太重。
 * 纵轴固定 0~100%;points 为 (unix_ts, util%) 稀疏序列。
 */

import { colorPrimary } from "@superdl/ui";

export function GpuSparkline({
  points,
  width = 110,
  height = 28,
}: {
  points: readonly (readonly number[])[];
  width?: number;
  height?: number;
}) {
  if (points.length === 0) return null;
  const t0 = points[0]?.[0] ?? 0;
  const t1 = points[points.length - 1]?.[0] ?? t0;
  const span = Math.max(t1 - t0, 1);
  const xy = points.map((p) => {
    const x = points.length === 1 ? width / 2 : (((p[0] ?? t0) - t0) / span) * (width - 2) + 1;
    const v = Math.min(Math.max(p[1] ?? 0, 0), 100);
    const y = height - 2 - (v / 100) * (height - 4);
    return [x, y] as const;
  });
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `1,${height - 2} ${line} ${(xy[xy.length - 1]?.[0] ?? width - 1).toFixed(1)},${height - 2}`;

  return (
    <svg width={width} height={height} aria-hidden style={{ display: "block" }}>
      <polygon points={area} fill={colorPrimary} opacity={0.1} />
      <polyline points={line} fill="none" stroke={colorPrimary} strokeWidth={1.5} />
    </svg>
  );
}
