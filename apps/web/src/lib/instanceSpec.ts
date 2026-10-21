/** GPU model shown for an instance: the display model snapshotted at creation, gpu_model for instances whose snapshot predates it. */
export function specGpuModel(spec: Record<string, unknown>): string {
  const shown = spec.display_model ?? spec.gpu_model;
  return typeof shown === "string" ? shown : "";
}
