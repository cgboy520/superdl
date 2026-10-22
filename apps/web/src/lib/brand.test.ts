import { describe, expect, it } from "vitest";

import { DEFAULT_SITE_NAME, siteBrand } from "./brand";

describe("siteBrand", () => {
  it("falls back to the built-in name and no slogan before /site-config loads or when unset", () => {
    expect(siteBrand(undefined)).toEqual({ name: DEFAULT_SITE_NAME, slogan: null });
    expect(siteBrand({ site_name: null, site_slogan: null })).toEqual({ name: DEFAULT_SITE_NAME, slogan: null });
    expect(siteBrand({ site_name: "", site_slogan: "" })).toEqual({ name: DEFAULT_SITE_NAME, slogan: null });
  });

  it("uses the admin-set name and slogan as given", () => {
    expect(siteBrand({ site_name: "YesGPU", site_slogan: "GPUs by the second" })).toEqual({
      name: "YesGPU",
      slogan: "GPUs by the second",
    });
  });
});
