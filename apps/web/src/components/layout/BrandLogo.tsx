/** 品牌标:SVG 图形(与 favicon 同形)+ 词标。light=深底白字(顶栏/Hero),dark=浅底靛字。 */

import { colorPrimary } from "@superdl/ui";

const S_PATH =
  "M21.5 10.6c-.9-1.6-2.7-2.6-5-2.6-3.1 0-5.3 1.7-5.3 4.2 0 2.2 1.5 3.4 4.6 4l1.9.4c1.9.4 " +
  "2.6.9 2.6 1.9 0 1.2-1.2 2-3 2-1.8 0-3.1-.8-3.4-2.1H10c.3 2.9 2.7 4.6 6.2 4.6 3.6 0 " +
  "5.9-1.8 5.9-4.5 0-2.2-1.5-3.4-4.7-4.1l-1.8-.4c-1.8-.4-2.6-1-2.6-1.9 0-1.1 1.1-1.9 " +
  "2.8-1.9 1.6 0 2.8.7 3.1 1.9h2.6z";

const BRAND_NAME = "SuperDL"; // 品牌字标,任何语言不译

export function BrandLogo({ variant = "dark" }: { variant?: "light" | "dark" }) {
  const light = variant === "light";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <svg width={26} height={26} viewBox="0 0 32 32" aria-hidden>
        <defs>
          <linearGradient id="sdl-mark" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#4338CA" />
            <stop offset="1" stopColor="#6D28D9" />
          </linearGradient>
        </defs>
        <rect
          width="32"
          height="32"
          rx="7"
          fill={light ? "rgba(255,255,255,0.92)" : "url(#sdl-mark)"}
        />
        <path d={S_PATH} fill={light ? colorPrimary : "#fff"} />
      </svg>
      <span
        style={{
          fontSize: 18,
          fontWeight: 700,
          color: light ? "#fff" : colorPrimary,
          letterSpacing: 0.3,
        }}
      >
        {BRAND_NAME}
      </span>
    </span>
  );
}
