/** Colour maths (WCAG 2.x): relative luminance, contrast ratio, black / white text by background. Shared by tokens.test and HexTag. */

import { textOnAccent, webDarkColors } from "./tokens";

function relLuminanceHex(hex: string): number {
  const c = hex.replace("#", "");
  const full =
    c.length === 3
      ? c
          .split("")
          .map((ch) => ch + ch)
          .join("")
      : c;
  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  const lin = (v: number) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** hex taken as-is; rgba(0,0,0,a) is composited onto white first */
export function relativeLuminance(color: string): number {
  const m = /^rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*([\d.]+)\s*\)$/.exec(color);
  const alpha = m?.[1];
  if (alpha !== undefined) {
    const v = Math.round((1 - Number(alpha)) * 255);
    const pair = v.toString(16).padStart(2, "0");
    return relLuminanceHex(`#${pair}${pair}${pair}`);
  }
  return relLuminanceHex(color);
}

export function contrastRatio(fg: string, bg: string): number {
  const [l1, l2] = [relativeLuminance(fg), relativeLuminance(bg)];
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

/** Text colour on a solid background: white or deep ink, whichever has the higher contrast. */
export function textOnColor(bg: string): string {
  return contrastRatio(textOnAccent, bg) >= contrastRatio(webDarkColors.bgBase, bg)
    ? textOnAccent
    : webDarkColors.bgBase;
}
