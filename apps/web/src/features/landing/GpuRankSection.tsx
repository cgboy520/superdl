/** GPU 算力排名:gpuSpecs 静态表驱动(理论峰值口径)+ 「每 TFLOPS 时价」(只算在售型号,取该型号最低单卡价 ÷ FP16 峰值)。
 *  在售型号带标记并链回价格墙;窄屏保留单位(数字不带单位读不出意义),行内自动换行。 */

import type { GpuSpec } from "@superdl/ui";
import {
  amountToScaledNumber,
  chartAccentColors,
  compareAmounts,
  fontFamilyMono,
  fontSize,
  fontWeight,
  gpuSpecs,
  medalColors,
  normalizeGpuModel,
  space,
  textOnAccent,
  useThemeColors,
} from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { Segmented, Tag, theme, Typography } from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSkus } from "../../api/queries";
import { LandingSection } from "./LandingSection";

type Metric = "fp16" | "fp32" | "price";

interface RankRow {
  model: string;
  label: string;
  vramGb: number;
  /** 排序值:算力档取 TFLOPS,性价比档取「每 TFLOPS 时价」 */
  value: number;
  /** 条长比例 0~1:算力越高越长;性价比越便宜越长 */
  ratio: number;
  onSale: boolean;
  text: string;
}

export function GpuRankSection() {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const colors = useThemeColors();
  const [metric, setMetric] = useState<Metric>("fp16");
  const { data: skus } = useSkus();

  /** 型号 → 最低单卡时价(万分位整数转元,只用于展示层比值) */
  const minPriceByModel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of skus ?? []) {
      if (!s.gpu_model) continue;
      const key = normalizeGpuModel(s.gpu_model);
      const cur = m.get(key);
      if (cur === undefined || compareAmounts(s.price_hourly, cur) < 0) m.set(key, s.price_hourly);
    }
    return m;
  }, [skus]);

  const rows = useMemo<RankRow[]>(() => {
    const entries = Object.entries(gpuSpecs);
    if (metric === "price") {
      const priced: { model: string; spec: GpuSpec; perTflops: number }[] = [];
      for (const [model, spec] of entries) {
        const price = minPriceByModel.get(model);
        if (price === undefined) continue; // 不在售就没有时价,不进榜
        // 展示层派生比值(不是入账金额):元 = 万分位整数 / 10000
        priced.push({ model, spec, perTflops: amountToScaledNumber(price) / 10000 / spec.fp16Tflops });
      }
      priced.sort((a, b) => a.perTflops - b.perTflops);
      const best = priced[0]?.perTflops ?? 1;
      return priced.map((r) => ({
        model: r.model,
        label: r.spec.label,
        vramGb: r.spec.vramGb,
        value: r.perTflops,
        // 越便宜条越长
        ratio: best / r.perTflops,
        onSale: true,
        text: t("landing.ranking.perTflopsValue", { price: r.perTflops.toFixed(4) }),
      }));
    }
    const key = metric === "fp16" ? "fp16Tflops" : "fp32Tflops";
    const list = entries.map(([model, spec]) => ({ model, spec, value: spec[key] })).sort((a, b) => b.value - a.value);
    const max = list[0]?.value ?? 1;
    return list.map((r) => ({
      model: r.model,
      label: r.spec.label,
      vramGb: r.spec.vramGb,
      value: r.value,
      ratio: r.value / max,
      onSale: minPriceByModel.has(r.model),
      // 两处都写成字面量键:i18next-cli 静态提取看不懂三元里的键,会当未使用删掉
      text:
        metric === "fp16"
          ? t("landing.ranking.fp16Value", { value: r.value })
          : t("landing.ranking.fp32Value", { value: r.value }),
    }));
  }, [metric, minPriceByModel, t]);

  return (
    <LandingSection
      id="ranking"
      background={token.colorBgContainer}
      title={t("landing.ranking.title")}
      subtitle={t("landing.ranking.subtitle")}
    >
      <div style={{ display: "flex", justifyContent: "center", marginBottom: space.xl }}>
        {/* Segmented 根节点是 generic div:aria-label 挂上去反而是 aria-prohibited-attr,靠区标题定位 */}
        <Segmented
          value={metric}
          onChange={(v: Metric) => setMetric(v)}
          options={[
            { value: "fp16", label: t("landing.ranking.tabFp16") },
            { value: "fp32", label: t("landing.ranking.tabFp32") },
            { value: "price", label: t("landing.ranking.tabPrice") },
          ]}
        />
      </div>
      <div style={{ maxWidth: 860, margin: "0 auto", display: "flex", flexDirection: "column", gap: space.md }}>
        {rows.map((r, i) => (
          <div key={r.model} style={{ display: "flex", alignItems: "center", gap: space.md, flexWrap: "wrap" }}>
            <span
              style={{
                width: 24,
                height: 24,
                borderRadius: "50%",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: fontSize.caption,
                fontWeight: fontWeight.semibold,
                flexShrink: 0,
                color: i < 3 ? textOnAccent : token.colorTextSecondary,
                background: i < 3 ? medalColors[i] : token.colorFillSecondary,
              }}
            >
              {i + 1}
            </span>
            <span style={{ flex: "1 1 160px", minWidth: 120, fontFamily: fontFamilyMono }}>
              {t("landing.ranking.modelVram", { label: r.label, vram: r.vramGb })}
            </span>
            <div
              style={{
                flex: "2 1 180px",
                minWidth: 80,
                background: token.colorFillQuaternary,
                borderRadius: 4,
                height: 14,
              }}
            >
              <div
                style={{
                  width: `${Math.max(r.ratio * 100, 2)}%`,
                  height: "100%",
                  borderRadius: 4,
                  background: `linear-gradient(90deg, ${colors.primary}, ${chartAccentColors.indigo})`,
                }}
              />
            </div>
            <span style={{ flex: "0 0 auto", textAlign: "right", fontFamily: fontFamilyMono }}>{r.text}</span>
            <span style={{ flex: "0 0 auto", width: 44 }}>
              {r.onSale && (
                <Link to="/" hash="pricing">
                  <Tag color={colors.primary} style={{ marginInlineEnd: 0 }}>
                    {t("landing.ranking.onSale")}
                  </Tag>
                </Link>
              )}
            </span>
          </div>
        ))}
      </div>
      <Typography.Paragraph
        type="secondary"
        style={{ textAlign: "center", marginTop: space.xl, fontSize: fontSize.caption }}
      >
        {metric === "price" ? t("landing.ranking.perTflopsFootnote") : t("landing.ranking.footnote")}
      </Typography.Paragraph>
    </LandingSection>
  );
}
