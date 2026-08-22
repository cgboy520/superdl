/** GPU 算力排名:gpuSpecs 静态表驱动(理论峰值口径,脚注声明);在售型号标记联动价格墙。 */

import { colorPrimary, gpuSpecs } from "@superdl/ui";
import { Grid, Tabs, Tag, theme, Typography } from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSkus } from "../../api/queries";

const MEDALS = ["#D97706", "#9CA3AF", "#B45309"];

export function GpuRankSection() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const [metric, setMetric] = useState<"fp16" | "fp32">("fp16");
  const { data: skus } = useSkus();
  // 窄屏紧凑模式:收缩定宽列,避免整行把页面撑出横向滚动
  const wide = Grid.useBreakpoint().md;

  const onSale = useMemo(
    () => new Set((skus ?? []).map((s) => s.gpu_model.replace(/[\s-]/g, "").toUpperCase())),
    [skus],
  );

  const rows = useMemo(() => {
    const key = metric === "fp16" ? "fp16Tflops" : "fp32Tflops";
    return Object.entries(gpuSpecs)
      .map(([model, spec]) => ({ model, spec, value: spec[key] }))
      .sort((a, b) => b.value - a.value);
  }, [metric]);
  const max = rows[0]?.value ?? 1;

  return (
    <section id="ranking" style={{ background: token.colorBgContainer }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "48px 24px" }}>
        <Typography.Title level={2} style={{ textAlign: "center", marginBottom: 4 }}>
          {t("landing.ranking.title")}
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center", marginBottom: 24 }}>
          {t("landing.ranking.subtitle")}
        </Typography.Paragraph>
        <Tabs
          centered
          activeKey={metric}
          onChange={(k) => setMetric(k as "fp16" | "fp32")}
          items={[
            { key: "fp16", label: t("landing.ranking.tabFp16") },
            { key: "fp32", label: t("landing.ranking.tabFp32") },
          ]}
        />
        <div style={{ maxWidth: 860, margin: "0 auto", display: "flex", flexDirection: "column", gap: 12 }}>
          {rows.map((r, i) => (
            <div key={r.model} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 12,
                  fontWeight: 700,
                  flexShrink: 0,
                  color: i < 3 ? "#fff" : token.colorTextSecondary,
                  background: i < 3 ? MEDALS[i] : token.colorFillSecondary,
                }}
              >
                {i + 1}
              </span>
              <span
                style={{
                  width: wide ? 150 : 104,
                  flexShrink: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  fontSize: wide ? 14 : 13,
                }}
              >
                {t("landing.ranking.modelVram", { label: r.spec.label, vram: r.spec.vramGb })}
              </span>
              <div style={{ flex: 1, minWidth: 32, background: token.colorFillQuaternary, borderRadius: 4, height: 14 }}>
                <div
                  style={{
                    width: `${Math.max((r.value / max) * 100, 2)}%`,
                    height: "100%",
                    borderRadius: 4,
                    background: `linear-gradient(90deg, ${colorPrimary}, #818CF8)`,
                  }}
                />
              </div>
              <span style={{ width: wide ? 170 : 56, textAlign: "right", flexShrink: 0, fontSize: wide ? 14 : 13 }}>
                {r.value}
                {wide ? (metric === "fp16" ? " Tensor TFLOPS" : " TFLOPS") : ""}
              </span>
              <span style={{ width: wide ? 56 : 40, flexShrink: 0, fontSize: wide ? 14 : 12 }}>
                {onSale.has(r.model) && (
                  <a href="/#pricing">
                    <Tag color={colorPrimary} style={{ marginInlineEnd: 0 }}>
                      {t("landing.ranking.onSale")}
                    </Tag>
                  </a>
                )}
              </span>
            </div>
          ))}
        </div>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center", marginTop: 24, fontSize: 12 }}>
          {t("landing.ranking.footnote")}
        </Typography.Paragraph>
      </div>
    </section>
  );
}
