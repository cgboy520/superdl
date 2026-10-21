/** GPU model shown on instance pages. */
import { describe, expect, it } from "vitest";

import { specGpuModel } from "./instanceSpec";

describe("specGpuModel", () => {
  it("shows the snapshotted display model, gpu_model for snapshots that predate it", () => {
    expect(specGpuModel({ gpu_model: "CMP170HX", display_model: "GA100-PCIE-64GB" })).toBe("GA100-PCIE-64GB");
    expect(specGpuModel({ gpu_model: "RTX4090" })).toBe("RTX4090");
    expect(specGpuModel({})).toBe("");
  });
});
