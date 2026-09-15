/** WCAG AA 对比度回归:状态徽标/热力格/奖牌/次级文本 token ≥4.5:1。 */

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

/** 半透明白叠在实色底上的等效实色。 */
function whiteOver(alpha: number, bgHex: string): string {
  const c = bgHex.replace("#", "");
  const mix = (i: number) => Math.round(alpha * 255 + (1 - alpha) * parseInt(c.slice(i, i + 2), 16));
  return `#${[0, 2, 4].map((i) => mix(i).toString(16).padStart(2, "0")).join("")}`;
}

describe("tokens 对比度(WCAG AA ≥4.5:1)", () => {
  it("状态徽标深底白字", () => {
    for (const [k, v] of Object.entries(statusColors)) {
      expect(contrast("#FFFFFF", v), `statusColors.${k}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("SKU 档位徽标深底白字", () => {
    for (const [k, v] of Object.entries(skuTierMap)) {
      expect(contrast("#FFFFFF", v.color), `skuTierMap.${k}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("节点热力格深底浅字", () => {
    for (const [k, v] of Object.entries(heatColors)) {
      expect(contrast("#FFFFFF", v), `heatColors.${k}`).toBeGreaterThanOrEqual(AA);
    }
    expect(contrast(adminColors.textMuted, adminColors.gridLine)).toBeGreaterThanOrEqual(AA);
  });

  it("落地页奖牌深底白字", () => {
    medalColors.forEach((v, i) => {
      expect(contrast("#FFFFFF", v), `medalColors[${i}]`).toBeGreaterThanOrEqual(AA);
    });
  });

  it("管理端弱化/次级文本在深色底上", () => {
    expect(contrast(adminColors.textMuted, adminColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(adminColors.textMuted, adminColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(adminColors.textSecondary, adminColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(adminColors.textSecondary, adminColors.bgBase)).toBeGreaterThanOrEqual(AA);
  });

  it("用户端次级/描述文本在白底上", () => {
    expect(contrast(webTheme.token.colorTextSecondary, "#FFFFFF")).toBeGreaterThanOrEqual(AA);
    expect(contrast(webTheme.token.colorTextDescription, "#FFFFFF")).toBeGreaterThanOrEqual(AA);
    expect(contrast(webTheme.token.colorPrimary, "#FFFFFF")).toBeGreaterThanOrEqual(AA);
  });

  it("用户端暗色板:正文/次级文本/菜单选中字在各自底上", () => {
    expect(contrast(webDarkColors.text, webDarkColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.text, webDarkColors.bgContainer)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.text, webDarkColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgContainer)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.menuSelectedColor, webDarkColors.menuSelectedBg)).toBeGreaterThanOrEqual(AA);
  });

  it("CSS 变量桥:主色/文本在各自底色上(focus 描边与选中态按文本级 AA)", () => {
    expect(contrast(cssVars.light["--sdl-color-primary"], cssVars.light["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.light["--sdl-color-text"], cssVars.light["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.dark["--sdl-color-primary"], cssVars.dark["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.dark["--sdl-color-text"], cssVars.dark["--sdl-color-bg"])).toBeGreaterThanOrEqual(AA);
  });

  it("图表强调色在管理端深底上(非文本图形,AA 要求 ≥3:1)", () => {
    for (const [k, v] of Object.entries(chartAccentColors)) {
      expect(contrast(v, adminColors.bgBase), `chartAccentColors.${k}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("公开层墨色面板(行情板 / 命令块):白字、弱化白字、库存绿、链接色于 brand.ink", () => {
    expect(contrast(brand.onHero, brand.ink)).toBeGreaterThanOrEqual(AA);
    expect(contrast(themeColors["web-dark"].positive, brand.ink)).toBeGreaterThanOrEqual(AA);
    expect(contrast(brand.indigo50, brand.ink)).toBeGreaterThanOrEqual(AA);
    const alpha = Number(/^rgba\(255,\s*255,\s*255,\s*([\d.]+)\)$/.exec(brand.inkTextMuted)?.[1] ?? "0");
    expect(alpha).toBeGreaterThan(0);
    expect(contrast(whiteOver(alpha, brand.ink), brand.ink)).toBeGreaterThanOrEqual(AA);
  });

  it("品牌渐变端色上的白字(顶栏/Hero/登录左栏大量白字落在渐变上)", () => {
    for (const [k, v] of Object.entries(brandGradientStops)) {
      expect(contrast("#FFFFFF", v), `brandGradientStops.${k}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("管理端:数据强调色非文本图形 ≥3:1,菜单选中配对与表头配对 ≥4.5:1", () => {
    expect(contrast(adminColors.dataAccent, adminColors.bgBase)).toBeGreaterThanOrEqual(3);
    expect(contrast(adminColors.dataAccent, adminColors.bgElevated)).toBeGreaterThanOrEqual(3);
    expect(contrast(adminColors.dataAccent, adminColors.menuSelectedBg)).toBeGreaterThanOrEqual(AA);
    expect(
      contrast(adminThemeComponents.Table.headerColor, adminThemeComponents.Table.headerBg),
    ).toBeGreaterThanOrEqual(AA);
  });

  it("图表系列色在各自主题底上(非文本图形 ≥3:1)", () => {
    for (const [k, v] of Object.entries(chartSeriesColors.light)) {
      expect(contrast(v, "#FFFFFF"), `chartSeriesColors.light.${k}`).toBeGreaterThanOrEqual(3);
    }
    for (const [k, v] of Object.entries(chartSeriesColors.dark)) {
      expect(contrast(v, webDarkColors.bgBase), `chartSeriesColors.dark.${k}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("节点状态 / 告警严重度徽标深底白字", () => {
    for (const [k, v] of Object.entries({ ...nodeStatusMap, ...severityMap })) {
      expect(contrast("#FFFFFF", v.color), k).toBeGreaterThanOrEqual(AA);
    }
  });

  it("HexTag 字色按底色亮度:亮绿 / 亮青底取深墨字,深红底取白字,且都 ≥4.5:1", () => {
    for (const bg of [adminColors.positive, adminColors.dataAccent, adminColors.alertAccent]) {
      expect(textOnColor(bg)).toBe(webDarkColors.bgBase);
      expect(contrast(textOnColor(bg), bg)).toBeGreaterThanOrEqual(AA);
    }
    expect(textOnColor(statusColors.red)).toBe("#FFFFFF");
    for (const v of Object.values(statusColors)) expect(contrast(textOnColor(v), v)).toBeGreaterThanOrEqual(AA);
  });

  it("themeColors:主色 / 正负向 / 警示于各自底面 ≥3:1(图形),主色于 primarySoft ≥4.5:1(文本)", () => {
    for (const [name, tc] of Object.entries(themeColors)) {
      const bg = tc.mode === "dark" ? (name === "admin" ? adminColors.bgBase : webDarkColors.bgBase) : "#FFFFFF";
      for (const k of ["primary", "positive", "negative", "warning", "info"] as const) {
        expect(contrast(tc[k], bg), `${name}.${k}`).toBeGreaterThanOrEqual(3);
      }
      expect(contrast(tc.primary, tc.primarySoft), `${name}.primary/primarySoft`).toBeGreaterThanOrEqual(AA);
    }
  });
});
