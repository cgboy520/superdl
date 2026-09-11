/**
 * 加速卡公开规格静态表(厂商白皮书理论峰值:FP32 shader 单精,FP16 Tensor 稠密)。
 * 展示处带「理论峰值」脚注(landing.ranking.footnote);key 与后端 `gpu_model` 一致,查询经 getGpuSpec 归一。
 */

export interface GpuSpec {
  /** 展示名(带空格排版) */
  label: string;
  vramGb: number;
  fp32Tflops: number;
  fp16Tflops: number;
  arch: string;
}

export const gpuSpecs: Record<string, GpuSpec> = {
  H100: { label: "H100 SXM", vramGb: 80, fp32Tflops: 67, fp16Tflops: 989.4, arch: "Hopper" },
  H800: { label: "H800", vramGb: 80, fp32Tflops: 51.2, fp16Tflops: 756.5, arch: "Hopper" },
  H20: { label: "H20", vramGb: 96, fp32Tflops: 44, fp16Tflops: 148, arch: "Hopper" },
  A100: { label: "A100 SXM4", vramGb: 80, fp32Tflops: 19.5, fp16Tflops: 312, arch: "Ampere" },
  A800: { label: "A800", vramGb: 80, fp32Tflops: 19.5, fp16Tflops: 312, arch: "Ampere" },
  L40: { label: "L40", vramGb: 48, fp32Tflops: 90.5, fp16Tflops: 181.05, arch: "Ada" },
  RTX5090: { label: "RTX 5090", vramGb: 32, fp32Tflops: 104.8, fp16Tflops: 209.5, arch: "Blackwell" },
  RTX4090: { label: "RTX 4090", vramGb: 24, fp32Tflops: 82.6, fp16Tflops: 165.2, arch: "Ada" },
  RTX3090: { label: "RTX 3090", vramGb: 24, fp32Tflops: 35.6, fp16Tflops: 71, arch: "Ampere" },
  V100: { label: "V100", vramGb: 32, fp32Tflops: 15.7, fp16Tflops: 125, arch: "Volta" },
};

/** 可选卡数档位(市场筛选与创建页共用);SKU 上限不在档位内时创建页补一档。 */
export const GPU_COUNT_STEPS: readonly number[] = [1, 2, 4, 8];

export function normalizeGpuModel(model: string): string {
  return model.replace(/[\s-]/g, "").toUpperCase();
}

/** 归一后查表;查不到返回 undefined。 */
export function getGpuSpec(model: string | null | undefined): GpuSpec | undefined {
  if (!model) return undefined;
  return gpuSpecs[normalizeGpuModel(model)];
}
