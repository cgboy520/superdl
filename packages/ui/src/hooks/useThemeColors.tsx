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
