/** 实例监控面板:四条序列(GPU / 显存 / CPU / 内存)按 1h / 6h / 24h 拉取。
 *  指标只做展示与对账,不参与计费;503 = 监控源未接入 / 断源(专用文案),其余错误绝不静默渲染成空图。
 *  实例详情页与服务详情页(当前版本实例)共用。 */

import { isApiError } from "@superdl/api-client";
import { DataErrorAlert, EChart } from "@superdl/ui/components";
import { Alert, Card, Radio, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useInstanceMetrics } from "../../api/queries";
import { useThemeMode } from "../../stores/theme";

const SERIES_META = {
  gpu_util: { nameKey: "instances.seriesGpu", unit: "%" },
  vram_used_mb: { nameKey: "instances.seriesVram", unit: "MB" },
  cpu_pct: { nameKey: "instances.seriesCpu", unit: "%" },
  mem_used_mb: { nameKey: "instances.seriesMem", unit: "MB" },
} as const;

export function MetricsPanel({ uuid, running }: { uuid: string; running: boolean }) {
  const { t } = useTranslation();
  const mode = useThemeMode();
  const [range, setRange] = useState<"1h" | "6h" | "24h">("1h");
  const { data, error, isLoading, refetch } = useInstanceMetrics(
    uuid,
    { range },
    { enabled: running, refetchInterval: 60_000, retry: 0 },
  );

  if (!running) {
    return <Alert type="info" showIcon title={t("instances.metricsNotRunning")} />;
  }
  if (error && isApiError(error) && error.status === 503) {
    return <Alert type="warning" showIcon title={t("copy.monitoringDown")} />;
  }
  if (error) {
    return <DataErrorAlert onRetry={() => void refetch()} />;
  }
  const series = (data?.series ?? {}) as Record<string, [number, number][]>;
  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Radio.Group
        value={range}
        onChange={(e) => setRange(e.target.value as typeof range)}
        optionType="button"
        options={[
          { value: "1h", label: t("instances.range1h") },
          { value: "6h", label: t("instances.range6h") },
          { value: "24h", label: t("instances.range24h") },
        ]}
      />
      {Object.entries(SERIES_META).map(([key, meta]) => (
        <Card key={key} size="small" title={t(meta.nameKey)} loading={isLoading}>
          {!isLoading && (series[key]?.length ?? 0) === 0 ? (
            <Typography.Text type="secondary" style={{ display: "block", padding: "24px 0" }}>
              {t("instances.metricsNoData")}
            </Typography.Text>
          ) : (
          <EChart
            theme={mode === "dark" ? "web-dark" : "web-light"}
            style={{ height: 180 }}
            ariaLabel={t(meta.nameKey)}
            option={{
              grid: { left: 48, right: 16, top: 16, bottom: 24 },
              xAxis: { type: "time" },
              yAxis: { type: "value", axisLabel: { formatter: `{value}${meta.unit}` } },
              tooltip: { trigger: "axis" },
              series: [
                {
                  type: "line",
                  showSymbol: false,
                  areaStyle: { opacity: 0.08 },
                  data: (series[key] ?? []).map(([ts, v]) => [ts * 1000, v]),
                },
              ],
            }}
          />
          )}
        </Card>
      ))}
    </Space>
  );
}
