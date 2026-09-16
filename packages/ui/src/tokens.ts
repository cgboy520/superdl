/** Design tokens shared by both consoles: user light / dark themes and the admin dark theme. */

export const colorPrimary = "#4F46E5";

export const fontFamily =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", ' +
  '"Microsoft YaHei", "Helvetica Neue", Arial, sans-serif';

/** Monospace stack for identifiers / prices / commands (the public layer's self-hosted IBM Plex Mono first; base.css .mono stays in sync) */
export const fontFamilyMono =
  '"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';

/** Brand gradient end colours (the topBar / hero gradient strings and the BrandLogo SVG stops derive from them) */
export const brandGradientStops = {
  topBarFrom: "#4338CA",
  heroFrom: "#312E81",
  heroMid: "#4F46E5",
  heroTo: "#6D28D9",
} as const;

/** Brand gradient and brand surfaces (user public layer + console top bar) */
export const brand = {
  /** Full-width top bar background of console / public pages */
  topBarBg: `linear-gradient(90deg, ${brandGradientStops.topBarFrom} 0%, ${brandGradientStops.heroMid} 100%)`,
  /** Home Hero / login left column background */
  heroBg: `linear-gradient(135deg, ${brandGradientStops.heroFrom} 0%, ${brandGradientStops.heroMid} 55%, ${brandGradientStops.heroTo} 100%)`,
  /** Light indigo accent surface (selected menu background / highlight block) */
  indigo50: "#EEF2FF",
  /** Page background (= colorBgLayout) */
  pageBg: "#F5F6FA",
  /** Public deep ink panel (price board / footer) */
  ink: "#14162B",
  /** Text on the brand gradient / deep ink (gradient end colours vs white text AA regressed by tokens.test) */
  onHero: "#FFFFFF",
  onHeroMuted: "rgba(255,255,255,0.85)",
  /** Dividers and muted text on the deep ink panel */
  inkBorder: "rgba(255,255,255,0.12)",
  inkDivider: "rgba(255,255,255,0.08)",
  inkTextMuted: "rgba(255,255,255,0.65)",
} as const;
export const adminColors = {
  bgBase: "#0B1220",
  bgElevated: "#111A2E",
  dataAccent: "#22D3EE",
  alertAccent: "#F59E0B",
  /** Secondary text (table sub-rows / chart axis labels) */
  textSecondary: "#94A3B8",
  /** Muted text (notes / placeholders); contrast ≥4.5:1, regressed by tokens.test.ts */
  textMuted: "#8296AD",
  /** Grid lines / empty block background */
  gridLine: "#1E293B",
  /** Neutral chart bars */
  chartNeutral: "#334155",
  /** Dividers */
  divider: "#1F2A44",
  /** Up / positive on dark */
  positive: "#4ADE80",
  /** Down / negative and error on dark */
  negative: "#F87171",
  /** critical badge background (same source as statusColors.red) */
  critical: "#DC2626",
  /** Selected sidebar menu background */
  menuSelectedBg: "#22355E",
} as const;

/** Text colour on solid badges / heat cells (white, regressed by tokens.test.ts) */
export const textOnAccent = "#FFFFFF";

/** Inverse CTA on the brand gradient (shared by the top-bar "Sign up free" / Hero / CTA banner) */
export const brandInverseButtonStyle = {
  background: textOnAccent,
  color: colorPrimary,
  borderColor: "transparent",
  fontWeight: 600,
} as const;

/** Status semantic colours (one set for both consoles); white-text contrast ≥4.5:1, regressed by tokens.test.ts. */
export const statusColors = {
  green: "#15803D",
  blue: "#2563EB",
  gray: "#6B7280",
  orange: "#C2410C",
  red: "#DC2626",
  /** Fifth semantic (adjustments etc.) */
  purple: "#6D28D9",
} as const;

/** Chart accent colours (shared by the noc theme palette and the landing compute ranking bars) */
export const chartAccentColors = {
  indigo: "#818CF8",
  pink: "#F472B6",
} as const;

/** Chart series colours (source of truth of the web-light / web-dark EChart palettes; non-text graphics contrast ≥3:1, regressed by tokens.test.ts) */
export const chartSeriesColors = {
  light: { green: "#16A34A", orange: "#EA580C", neutral: "#64748B" },
  dark: { green: "#4ADE80", orange: "#FB923C", neutral: "#94A3B8" },
} as const;

/** GPU heat cell backgrounds on the nodes page */
export const heatColors = {
  /** Metrics present, low load */
  low: statusColors.green,
  /** Medium load */
  mid: "#B45309",
  /** High load */
  high: statusColors.red,
} as const;

/** Landing compute ranking medals (gold / silver / bronze) */
export const medalColors = ["#A16207", "#6B7280", "#92400E"] as const;

/** antd 6 ConfigProvider theme — user console (light) */
export const webTheme = {
  token: {
    colorPrimary,
    colorInfo: colorPrimary,
    colorLink: colorPrimary,
    colorSuccess: statusColors.green,
    colorWarning: statusColors.orange,
    colorError: statusColors.red,
    colorBgLayout: brand.pageBg,
    colorTextSecondary: "rgba(0,0,0,0.60)",
    colorTextDescription: "rgba(0,0,0,0.58)",
    borderRadius: 6,
    fontFamily,
  },
  components: {
    Layout: { siderBg: "#FFFFFF", footerBg: "transparent" },
    Menu: {
      itemSelectedBg: brand.indigo50,
      itemSelectedColor: colorPrimary,
      itemMarginInline: 8,
      itemBorderRadius: 6,
    },
    Table: { headerBg: "#F9FAFB", cellPaddingBlock: 12 },
    Card: { borderRadiusLG: 10 },
    Button: { fontWeight: 500 },
    Statistic: { contentFontSize: 28 },
  },
} as const;

/** Local theme tokens of the ink panel, used together with theme.darkAlgorithm. */
export const inkPanelTokens = {
  colorBgContainer: brand.ink,
  colorBgElevated: brand.ink,
  colorTextSecondary: brand.inkTextMuted,
  colorTextDescription: brand.inkTextMuted,
  colorLink: brand.indigo50,
  colorBorder: brand.inkBorder,
  colorSplit: brand.inkDivider,
} as const;

/** antd 6 ConfigProvider theme — admin console (dark, used with theme.darkAlgorithm) */
export const adminThemeToken = {
  colorPrimary,
  colorInfo: adminColors.dataAccent,
  colorBgBase: adminColors.bgBase,
  colorBgContainer: adminColors.bgElevated,
  colorWarning: adminColors.alertAccent,
  borderRadius: 6,
} as const;

/** Admin component-level overrides (passed to ConfigProvider together with adminThemeToken). Density "compact": tables 13px / cell padding 8, see docs/ui-ux-spec.md §2. */
export const adminThemeComponents = {
  Table: {
    headerBg: adminColors.gridLine,
    headerColor: adminColors.textSecondary,
    cellFontSize: 13,
    cellFontSizeSM: 13,
    cellPaddingBlock: 8,
    cellPaddingBlockSM: 6,
    cellPaddingInline: 12,
  },
  Menu: {
    itemSelectedBg: adminColors.menuSelectedBg,
    itemSelectedColor: adminColors.dataAccent,
    itemMarginInline: 8,
    itemBorderRadius: 6,
  },
  Card: { borderRadiusLG: 10 },
  Button: { fontWeight: 500 },
  Statistic: { contentFontSize: 28 },
  Tabs: { inkBarColor: adminColors.dataAccent, itemSelectedColor: adminColors.dataAccent },
} as const;

/** Spacing scale, all multiples of 4. */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

/** Two shadow tiers: content cards have none by default; sm = clickable card / option tile hover, upMd = sticky bottom bar casting upwards. */
export const shadow = {
  light: {
    sm: "0 1px 2px rgba(15, 20, 32, 0.06), 0 2px 8px rgba(15, 20, 32, 0.04)",
    upMd: "0 -4px 12px rgba(15, 20, 32, 0.06)",
  },
  dark: {
    sm: "0 1px 2px rgba(0, 0, 0, 0.4), 0 2px 8px rgba(0, 0, 0, 0.3)",
    upMd: "0 -4px 12px rgba(0, 0, 0, 0.45)",
  },
} as const;

/** Font-size hierarchy: page title / section title / body / caption / KPI, five tiers + the public display tier */
export const fontSize = {
  pageTitle: 20,
  sectionTitle: 16,
  body: 14,
  caption: 12,
  kpi: 28,
  /** Public Hero title */
  display: 48,
} as const;

/** Font-weight scale: body regular, buttons and emphasis medium, titles and KPI numbers semibold */
export const fontWeight = { regular: 400, medium: 500, semibold: 600 } as const;

/** Three icon sizes (sm inline / md buttons and menus / lg top bar and empty states) */
export const iconSize = { sm: 14, md: 16, lg: 20 } as const;

/** Responsive breakpoints. */
export const breakpoint = { xs: 480, sm: 576, md: 768, lg: 992, xl: 1200 } as const;

/** Layout constants: page container and card grid; four page widths: console / landing section / long-form page / full (admin wide tables) */
export const layout = {
  pageMaxWidth: 1280,
  pageMaxWidthWide: 1200,
  pageMaxWidthNarrow: 880,
  contentPadding: 24,
  cardGap: 16,
  cardRadius: 10,
  /** Vertical spacing of landing sections */
  sectionPaddingY: 48,
  /** Top bar height (same on both consoles); sticky header offset and anchor scroll compensation derive from it */
  topBarHeight: 56,
  /** scroll-margin-top of anchor scroll targets (top bar + one spacing step) */
  scrollMarginTop: 56 + 16,
  /** Sidebar width (same on both consoles) and the icon rail width when collapsed on desktop */
  siderWidth: 200,
  siderCollapsedWidth: 80,
  /** Narrow-screen navigation Drawer width */
  navDrawerWidth: 260,
} as const;

/** Four control widths: short codes, short text, regular and long text. */
export const controlWidth = { xs: 96, sm: 160, md: 260, lg: 320 } as const;

/** Two Drawer widths (CSS values, narrow screens collapse to 100vw automatically) */
export const drawerWidth = { md: "min(640px, 100vw)", lg: "min(760px, 100vw)" } as const;

/** Two motion tiers (used with MotionConfig reducedMotion="user"); usage in docs/ui-ux-spec.md §2. */
export const motion = {
  /** Status change fade-in */
  fast: 0.15,
  /** Regular transitions (selected state / badge colour / overlays) */
  normal: 0.2,
  /** Shared ease-out curve */
  easeOut: [0.16, 1, 0.3, 1],
} as const;

/** z-index constants of hand-drawn overlays. */
export const zIndex = {
  stickyBar: 50,
  topBar: 100,
  skipLink: 200,
} as const;

/** Web dark palette: deep indigo-grey base, brand indigo primary; contrast ≥4.5:1, regressed by tokens.test.ts. */
export const webDarkColors = {
  bgBase: "#0F1420",
  bgContainer: "#171E30",
  bgElevated: "#1F2942",
  text: "#E5E9F2",
  textSecondary: "#9AA7C2",
  border: "#2A3552",
  /** Dark Menu selected background / selected text */
  menuSelectedBg: "#26304D",
  menuSelectedColor: "#A5B4FC",
} as const;

/** antd 6 ConfigProvider theme — web dark (with theme.darkAlgorithm); only background / text / border families are overridden */
export const webDarkTheme = {
  token: {
    ...webTheme.token,
    colorBgBase: webDarkColors.bgBase,
    colorBgContainer: webDarkColors.bgContainer,
    colorBgElevated: webDarkColors.bgElevated,
    colorBgLayout: webDarkColors.bgBase,
    colorText: webDarkColors.text,
    colorTextSecondary: webDarkColors.textSecondary,
    colorTextDescription: webDarkColors.textSecondary,
    colorBorder: webDarkColors.border,
  },
  components: {
    ...webTheme.components,
    Layout: { siderBg: webDarkColors.bgContainer, footerBg: "transparent" },
    Menu: {
      itemSelectedBg: webDarkColors.menuSelectedBg,
      itemSelectedColor: webDarkColors.menuSelectedColor,
      itemMarginInline: 8,
      itemBorderRadius: 6,
    },
    Table: { headerBg: webDarkColors.bgElevated, cellPaddingBlock: 12 },
  },
} as const;

/** CSS variable bridge: values injected on :root by apps/web/src/routes/__root.tsx per theme; styles.css only references var(). The dark primary is #A5B4FC. */
export const cssVars = {
  light: {
    "--sdl-color-primary": colorPrimary,
    "--sdl-color-bg": brand.pageBg,
    "--sdl-color-text": "rgba(0,0,0,0.88)",
    "--sdl-color-text-secondary": "rgba(0,0,0,0.60)",
    "--sdl-color-required": statusColors.red,
    "--sdl-scroll-thumb": "rgba(0,0,0,0.25)",
  },
  dark: {
    "--sdl-color-primary": webDarkColors.menuSelectedColor,
    "--sdl-color-bg": webDarkColors.bgBase,
    "--sdl-color-text": webDarkColors.text,
    "--sdl-color-text-secondary": webDarkColors.textSecondary,
    "--sdl-color-required": adminColors.negative,
    "--sdl-scroll-thumb": "rgba(255,255,255,0.25)",
  },
} as const;

/** Chart axis / grid / tooltip backgrounds (light EChart preset; dark and admin use their own palettes) */
export const chartAxisColors = {
  light: {
    axis: "#E5E7EB",
    grid: "#F0F1F5",
    tooltipBg: "#FFFFFF",
    text: "rgba(0,0,0,0.60)",
    tooltipText: "rgba(0,0,0,0.88)",
  },
} as const;

/** JS-side semantic colours (obtained via useThemeColors(), never by importing single values): two sets for web by theme, one fixed set for admin. */
export type ThemeKey = "web-light" | "web-dark" | "admin";

export interface ThemeColors {
  mode: "light" | "dark";
  chartTheme: "web-light" | "web-dark" | "noc";
  primary: string;
  primarySoft: string;
  positive: string;
  negative: string;
  warning: string;
  info: string;
  neutral: string;
  textSecondary: string;
  surface: string;
  border: string;
}

export const themeColors: Record<ThemeKey, ThemeColors> = {
  "web-light": {
    mode: "light",
    chartTheme: "web-light",
    primary: colorPrimary,
    primarySoft: brand.indigo50,
    positive: statusColors.green,
    negative: statusColors.red,
    warning: statusColors.orange,
    info: statusColors.blue,
    neutral: chartSeriesColors.light.neutral,
    textSecondary: "rgba(0,0,0,0.60)",
    surface: "#FFFFFF",
    border: chartAxisColors.light.axis,
  },
  "web-dark": {
    mode: "dark",
    chartTheme: "web-dark",
    primary: webDarkColors.menuSelectedColor,
    primarySoft: webDarkColors.menuSelectedBg,
    positive: chartSeriesColors.dark.green,
    negative: adminColors.negative,
    warning: chartSeriesColors.dark.orange,
    info: chartAccentColors.indigo,
    neutral: chartSeriesColors.dark.neutral,
    textSecondary: webDarkColors.textSecondary,
    surface: webDarkColors.bgContainer,
    border: webDarkColors.border,
  },
  admin: {
    mode: "dark",
    chartTheme: "noc",
    primary: adminColors.dataAccent,
    primarySoft: adminColors.menuSelectedBg,
    positive: adminColors.positive,
    negative: adminColors.negative,
    warning: adminColors.alertAccent,
    info: adminColors.dataAccent,
    neutral: adminColors.chartNeutral,
    textSecondary: adminColors.textSecondary,
    surface: adminColors.bgElevated,
    border: adminColors.divider,
  },
};
