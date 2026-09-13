/** JS 侧语义色的唯一入口:ThemeProvider 由各端根组件注入主题键(web 随明暗切换,admin 固定),组件经 useThemeColors() 取色、useChartTheme() 取图表预设。 */

import { createContext, useContext, type ReactNode } from "react";

import { themeColors, type ThemeColors, type ThemeKey } from "../tokens";

const ThemeContext = createContext<ThemeKey>("web-light");

export function ThemeProvider({ value, children }: { value: ThemeKey; children: ReactNode }) {
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useThemeColors(): ThemeColors {
  return themeColors[useContext(ThemeContext)];
}

export function useChartTheme(): ThemeColors["chartTheme"] {
  return useThemeColors().chartTheme;
}
