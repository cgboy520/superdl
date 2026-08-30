/** WCAG 对比度回归:状态徽标/热力格/奖牌/次级文本 token 必须 ≥4.5:1(AA)。 */

import { describe, expect, it } from "vitest";

import {
  adminColors,
  adminThemeComponents,
  brandGradientStops,
  chartAccentColors,
  chartSeriesColors,
  cssVars,
  heatColors,
  medalColors,
  statusColors,
  textOnAccent,
  webDarkColors,
  webTheme,
} from "./tokens";
import { skuTierMap } from "./status";

function relLuminance(hex: string): number {
  const c = hex.replace("#", "");
  const r = parseInt(c.slice(0, 2), 16) / 255;
  const g = parseInt(c.slice(2, 4), 16) / 255;
  const b = parseInt(c.slice(4, 6), 16) / 255;
  const lin = (v: number) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** 非透明 hex 直接取;rgba(0,0,0,a) 先按通道与白底合成(亮度不可线性混合)再取 */
function luminanceOf(color: string): number {
  const m = /^rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*([\d.]+)\s*\)$/.exec(color);
  const alpha = m?.[1];
  if (alpha !== undefined) {
    const v = Math.round((1 - Number(alpha)) * 255);
    const pair = v.toString(16).padStart(2, "0");
    return relLuminance(`#${pair}${pair}${pair}`);
  }
  return relLuminance(color);
}

function contrast(fg: string, bg: string): number {
  const [l1, l2] = [luminanceOf(fg), luminanceOf(bg)];
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

const AA = 4.5;

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
    // 空闲格:弱化文本色于网格底
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
    // 浅靛强调面上的主色文字(菜单选中)
    expect(contrast(webTheme.token.colorPrimary, "#FFFFFF")).toBeGreaterThanOrEqual(AA);
  });

  it("用户端暗色板:正文/次级文本/菜单选中字在各自底上", () => {
    expect(contrast(webDarkColors.text, webDarkColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.text, webDarkColors.bgContainer)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.text, webDarkColors.bgElevated)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgContainer)).toBeGreaterThanOrEqual(AA);
    expect(
      contrast(webDarkColors.menuSelectedColor, webDarkColors.menuSelectedBg)
    ).toBeGreaterThanOrEqual(AA);
  });

  it("实心徽标文字色恒白(与各状态底配对已在上覆盖,此处锁白值)", () => {
    expect(textOnAccent).toBe("#FFFFFF");
  });

  it("CSS 变量桥:主色/文本在各自底色上(focus 描边与选中态按文本级 AA)", () => {
    // 浅色:主色/文本于 pageBg
    expect(contrast(cssVars.light["--sdl-color-primary"], cssVars.light["--sdl-color-bg"]))
      .toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.light["--sdl-color-text"], cssVars.light["--sdl-color-bg"]))
      .toBeGreaterThanOrEqual(AA);
    // 暗色:提浅主色/文本于 bgBase
    expect(contrast(cssVars.dark["--sdl-color-primary"], cssVars.dark["--sdl-color-bg"]))
      .toBeGreaterThanOrEqual(AA);
    expect(contrast(cssVars.dark["--sdl-color-text"], cssVars.dark["--sdl-color-bg"]))
      .toBeGreaterThanOrEqual(AA);
  });

  it("图表强调色在管理端深底上(非文本图形,AA 要求 ≥3:1)", () => {
    for (const [k, v] of Object.entries(chartAccentColors)) {
      expect(contrast(v, adminColors.bgBase), `chartAccentColors.${k}`).toBeGreaterThanOrEqual(3);
    }
  });

  it("品牌渐变端色上的白字(顶栏/Hero/登录左栏大量白字落在渐变上)", () => {
    for (const [k, v] of Object.entries(brandGradientStops)) {
      expect(contrast("#FFFFFF", v), `brandGradientStops.${k}`).toBeGreaterThanOrEqual(AA);
    }
  });

  it("管理端:数据强调色非文本图形 ≥3:1,菜单选中配对与表头配对 ≥4.5:1", () => {
    expect(contrast(adminColors.dataAccent, adminColors.bgBase)).toBeGreaterThanOrEqual(3);
    expect(contrast(adminColors.dataAccent, adminColors.bgElevated)).toBeGreaterThanOrEqual(3);
    // 菜单选中:青字于深靛底
    expect(contrast(adminColors.dataAccent, adminColors.menuSelectedBg)).toBeGreaterThanOrEqual(AA);
    // 表头:次级文本于表头底
    expect(
      contrast(adminThemeComponents.Table.headerColor, adminThemeComponents.Table.headerBg)
    ).toBeGreaterThanOrEqual(AA);
  });

  it("图表系列色在各自主题底上(非文本图形 ≥3:1)", () => {
    for (const [k, v] of Object.entries(chartSeriesColors.light)) {
      expect(contrast(v, "#FFFFFF"), `chartSeriesColors.light.${k}`).toBeGreaterThanOrEqual(3);
    }
    for (const [k, v] of Object.entries(chartSeriesColors.dark)) {
      expect(contrast(v, webDarkColors.bgBase), `chartSeriesColors.dark.${k}`)
        .toBeGreaterThanOrEqual(3);
    }
  });

  it("用户端暗色:描述文本显式取值(不依赖算法派生)在两种深底上", () => {
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgBase)).toBeGreaterThanOrEqual(AA);
    expect(contrast(webDarkColors.textSecondary, webDarkColors.bgElevated))
      .toBeGreaterThanOrEqual(AA);
  });
});
