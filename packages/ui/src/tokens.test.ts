/** WCAG AA contrast regression: status badge / heat cell / medal / secondary text tokens ≥ 4.5:1. */

import { describe, expect, it } from "vitest";

import {
  adminColors,
  adminThemeComponents,
  brand,
  brandGradientStops,
  chartAccentColors,
  chartSeriesColors,
  cssVars,
  heatColors,
  medalColors,
  statusColors,
  themeColors,
  webDarkColors,
  webTheme,
} from "./tokens";
import { contrastRatio, textOnColor } from "./color";
import { nodeStatusMap, severityMap, skuTierMap } from "./status";

const contrast = contrastRatio;

const AA = 4.5;

/** Equivalent solid colour of translucent white over a solid background. */
function whiteOver(alpha: number, bgHex: string): string {
  const c = bgHex.replace("#", "");
  const mix = (i: number) => Math.round(alpha * 255 + (1 - alpha) * parseInt(c.slice(i, i + 2), 16));
  return `#${[0, 2, 4].map((i) => mix(i).toString(16).padStart(2, "0")).join("")}`;
}

describe("token contrast (WCAG AA ≥4.5:1)", () => {
  it("status badges: white text on dark backgrounds", () => {
    for (const [k, v] of Object.entries(statusColors)) {
      expect(contrast("#FFFFFF", v), `statusColors.${k}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("SKU tier badges: white text on dark backgrounds", () => {
    for (const [k, v] of Object.entries(skuTierMap)) {
      expect(contrast("#FFFFFF", v.color), `skuTierMap.${k}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("node heat cells: light text on dark backgrounds", () => {
    for (const [k, v] of Object.entries(heatColors)) {
      expect(contrast("#FFFFFF", v), `heatColors.${k}`).toBeGreaterThanOrEqual(AA);
    }
    expect(contrast(adminColors.textMuted, adminColors.gridLine)).toBeGreaterThanOrEqual(AA);
  });

  it("landing page medals: white text on dark backgrounds", () => {
    medalColors.forEach((v, i) => {
      expect(contrast("#FFFFFF", v), `medalColors[${i}]`).toBeGreaterThanOrEqual(AA);
    });
  });

  it("admin muted / secondary text on the dark background", () => {
    expect(contrast(adminColors.textMuted, adminColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(adminColors.textMuted, adminColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(adminColors.textSecondary, adminColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(adminColors.textSecondary, adminColors.bgBase)).toBeGreaterThanOrEqual(AA);
  });

  it("web secondary / description text on white", () => {
    expect(contrast(webTheme.token.colorTextSecondary, "#FFFFFF")).toBeGreaterThanOrEqual(AA);
    expect(contrast(webTheme.token.colorTextDescription, "#FFFFFF")).toBeGreaterThanOrEqual(AA);
    expect(contrast(webTheme.token.colorPrimary, "#FFFFFF")).toBeGreaterThanOrEqual(AA);
  });

  it("web dark palette: body / secondary text / selected menu text on their backgrounds", () => {
    expect(contrast(webDarkColors.text, webDarkColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.text, webDarkColors.bgContainer)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.text, webDarkColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgContainer)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.menuSelectedColor, webDarkColors.menuSelectedBg)).toBeGreaterThanOrEqual(AA);
  });

  it("CSS variable bridge: primary / text on their backgrounds (focus ring and selected state at text-level AA)", () => {
    expect(contrast(cssVars.light["--sdl-color-primary"], cssVars.light["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.light["--sdl-color-text"], cssVars.light["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.dark["--sdl-color-primary"], cssVars.dark["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.dark["--sdl-color-text"], cssVars.dark["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
  });

  it("chart accent colours on the admin dark background (non-text graphics, AA requires ≥3:1)", () => {
    for (const [k, v] of Object.entries(chartAccentColors)) {
      expect(contrast(v, adminColors.bgBase), `chartAccentColors.${k}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("public ink panel (price board / command block): white, muted white, stock green and link colour on brand.ink", () => {
    expect(contrast(brand.onHero, brand.ink)).toBeGreaterThanOrEqual(AA);
    expect(contrast(themeColors["web-dark"].positive, brand.ink)).toBeGreaterThanOrEqual(AA);
    expect(contrast(brand.indigo50, brand.ink)).toBeGreaterThanOrEqual(AA);
    const alpha = Number(/^rgba\(255,\s*255,\s*255,\s*([\d.]+)\)$/.exec(brand.inkTextMuted)?.[1] ?? "0");
    expect(alpha).toBeGreaterThan(0);
    expect(contrast(whiteOver(alpha, brand.ink), brand.ink)).toBeGreaterThanOrEqual(AA);
  });

  it("white text on the brand gradient end colours (top bar / Hero / login left column put lots of white text on the gradient)", () => {
    for (const [k, v] of Object.entries(brandGradientStops)) {
      expect(contrast("#FFFFFF", v), `brandGradientStops.${k}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("admin: data accent non-text graphics ≥3:1, selected-menu and table-header pairs ≥4.5:1", () => {
    expect(contrast(adminColors.dataAccent, adminColors.bgBase)).toBeGreaterThanOrEqual(3);
    expect(contrast(adminColors.dataAccent, adminColors.bgElevated)).toBeGreaterThanOrEqual(3);
    expect(contrast(adminColors.dataAccent, adminColors.menuSelectedBg)).toBeGreaterThanOrEqual(AA);
    expect(
      contrast(adminThemeComponents.Table.headerColor, adminThemeComponents.Table.headerBg),
    ).toBeGreaterThanOrEqual(AA);
  });

  it("chart series colours on their theme backgrounds (non-text graphics ≥3:1)", () => {
    for (const [k, v] of Object.entries(chartSeriesColors.light)) {
      expect(contrast(v, "#FFFFFF"), `chartSeriesColors.light.${k}`).toBeGreaterThanOrEqual(3);
    }
    for (const [k, v] of Object.entries(chartSeriesColors.dark)) {
      expect(contrast(v, webDarkColors.bgBase), `chartSeriesColors.dark.${k}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("node status / alert severity badges: white text on dark backgrounds", () => {
    for (const [k, v] of Object.entries({ ...nodeStatusMap, ...severityMap })) {
      expect(contrast("#FFFFFF", v.color), k).toBeGreaterThanOrEqual(AA);
    }
  });

  it("HexTag text by background luminance: deep ink on light green / cyan, white on deep red, all ≥4.5:1", () => {
    for (const bg of [adminColors.positive, adminColors.dataAccent, adminColors.alertAccent]) {
      expect(textOnColor(bg)).toBe(webDarkColors.bgBase);
      expect(contrast(textOnColor(bg), bg)).toBeGreaterThanOrEqual(AA);
    }
    expect(textOnColor(statusColors.red)).toBe("#FFFFFF");
    for (const v of Object.values(statusColors)) expect(contrast(textOnColor(v), v)).toBeGreaterThanOrEqual(AA);
  });

  it("themeColors: primary / positive-negative / warning on their surfaces ≥3:1 (graphics), primary on primarySoft ≥4.5:1 (text)", () => {
    for (const [name, tc] of Object.entries(themeColors)) {
      const bg = tc.mode === "dark" ? (name === "admin" ? adminColors.bgBase : webDarkColors.bgBase) : "#FFFFFF";
      for (const k of ["primary", "positive", "negative", "warning", "info"] as const) {
        expect(contrast(tc[k], bg), `${name}.${k}`).toBeGreaterThanOrEqual(3);
      }
      expect(contrast(tc.primary, tc.primarySoft), `${name}.primary/primarySoft`).toBeGreaterThanOrEqual(AA);
    }
  });
});
