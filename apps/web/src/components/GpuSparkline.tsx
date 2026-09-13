/** GPU 利用率迷你 sparkline(实例列表列,近 1h),自绘 SVG。纵轴 0~100%;points 为 (unix_ts, util%) 稀疏序列,空序列由调用方过滤。 */

import { useThemeColors } from "@superdl/ui";

const WIDTH = 110;
const HEIGHT = 28;

export function GpuSparkline({ points }: { points: readonly (readonly [number, number])[] }) {
  const { primary } = useThemeColors();
  const first = points[0];
  if (!first) return null;
  const t0 = first[0];
  const t1 = (points[points.length - 1] ?? first)[0];
  const span = Math.max(t1 - t0, 1);
  const xy = points.map(([ts, util]) => {
    const x = points.length === 1 ? WIDTH / 2 : ((ts - t0) / span) * (WIDTH - 2) + 1;
    const v = Math.min(Math.max(util, 0), 100); // 越界数据钉住画布
    const y = HEIGHT - 2 - (v / 100) * (HEIGHT - 4);
    return [x, y] as const;
  });
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const lastX = (xy[xy.length - 1] ?? [WIDTH - 1, 0])[0];
  const area = `1,${HEIGHT - 2} ${line} ${lastX.toFixed(1)},${HEIGHT - 2}`;

  return (
    <svg width={WIDTH} height={HEIGHT} aria-hidden style={{ display: "block" }}>
      <polygon points={area} fill={primary} opacity={0.1} />
      <polyline points={line} fill="none" stroke={primary} strokeWidth={1.5} />
    </svg>
  );
}
