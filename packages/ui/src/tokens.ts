/**
 * 两端共享设计 token。
 * 用户端:浅色,主色靛蓝,顶栏/Hero 用 brand 渐变常量。
 * 管理端:深色 NOC 风,亮青作数据强调、琥珀作告警。
 */

export const colorPrimary = "#4F46E5";

/** 系统字体栈;数字对齐靠全局 tabular-nums(见 apps/web/src/styles.css) */
export const fontFamily =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", ' +
  '"Microsoft YaHei", "Helvetica Neue", Arial, sans-serif';

/** 品牌渐变与品牌面(用户端公开层 + 控制台顶栏) */
export const brand = {
  /** 控制台/公开页全宽顶栏底 */
  topBarBg: "linear-gradient(90deg, #4338CA 0%, #4F46E5 100%)",
  /** 主页 Hero / 登录页左栏底 */
  heroBg: "linear-gradient(135deg, #312E81 0%, #4F46E5 55%, #6D28D9 100%)",
  /** 浅靛强调面(菜单选中底/高亮块) */
  indigo50: "#EEF2FF",
  /** 页面底色(= colorBgLayout) */
  pageBg: "#F5F6FA",
} as const;
export const adminColors = {
  bgBase: "#0B1220",
  bgElevated: "#111A2E",
  dataAccent: "#22D3EE",
  alertAccent: "#F59E0B",
  /** 次要文本(表格副行/图表轴标) */
  textSecondary: "#94A3B8",
  /** 弱化文本(说明/占位);深底上须 ≥4.5:1(WCAG AA),见 tokens.test.ts 对比度回归 */
  textMuted: "#8296AD",
  /** 网格线/空块底 */
  gridLine: "#1E293B",
  /** 图表中性条 */
  chartNeutral: "#334155",
  /** 分隔线 */
  divider: "#1F2A44",
  /** 深色下的涨/正向(浅绿,深底可读) */
  positive: "#4ADE80",
  /** 深色下的跌/负向与错误(浅红,深底可读) */
  negative: "#F87171",
  /** critical 徽标底(与 statusColors.red 同源) */
  critical: "#DC2626",
} as const;

/** 状态语义色(两端同一套,管理端深色下由 antd 算法自动调亮)。
 * 徽标为「深底白字」:全部取值白字对比度 ≥4.5:1(WCAG AA,tokens.test.ts 回归守护)。 */
export const statusColors = {
  green: "#15803D",
  blue: "#2563EB",
  gray: "#6B7280",
  orange: "#C2410C",
  red: "#DC2626",
} as const;

/** 节点页 GPU 热力格:深底浅字,白字对比度 ≥4.5:1 */
export const heatColors = {
  /** 有指标且低载 */
  low: statusColors.green,
  /** 中载(深琥珀;亮琥珀 #F59E0B 白字仅 2.3:1,不达标) */
  mid: "#B45309",
  /** 高载 */
  high: statusColors.red,
} as const;

/** 落地页算力排名奖牌(金/银/铜):深底白字 ≥4.5:1 */
export const medalColors = ["#A16207", "#6B7280", "#92400E"] as const;

/** antd 6 ConfigProvider theme —— 用户端(浅色) */
export const webTheme = {
  token: {
    colorPrimary,
    colorInfo: colorPrimary,
    colorLink: colorPrimary,
    colorSuccess: statusColors.green,
    colorWarning: statusColors.orange,
    colorError: statusColors.red,
    colorBgLayout: brand.pageBg,
    // 次级文本默认 rgba(0,0,0,0.45) 白底仅 ~3.7:1;取深一档到 ≈5.7:1(WCAG AA)
    colorTextSecondary: "rgba(0,0,0,0.60)",
    // 默认 rgba(0,0,0,0.45) 白底对比度不足 AA;调实到 ≈5.3:1
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

/** antd 6 ConfigProvider theme —— 管理端(深色,配合 theme.darkAlgorithm 使用) */
export const adminThemeToken = {
  colorPrimary,
  colorInfo: adminColors.dataAccent,
  colorBgBase: adminColors.bgBase,
  colorBgContainer: adminColors.bgElevated,
  colorWarning: adminColors.alertAccent,
  borderRadius: 6,
} as const;

/** 间距阶梯(4 的倍数)。纪律:新代码的布局尺寸(padding/gap/margin)一律走这里,
 * 不再散落魔法数;存量 inline style 按「碰到的文件顺手收敛」推进(见 docs/ui-ux-spec.md) */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

/** 字号层级:页面标题/区块标题/正文/辅助/KPI 五档(散落硬编码的收敛目标) */
export const fontSize = {
  pageTitle: 20,
  sectionTitle: 16,
  body: 14,
  caption: 12,
  kpi: 28,
} as const;

/** 版式常量:页容器与卡片网格 */
export const layout = {
  pageMaxWidth: 1280,
  contentPadding: 24,
  cardGap: 16,
  cardRadius: 10,
} as const;

/** 用户端暗色板(「开发者夜间工作台」):深靛灰基板,与 admin 的 NOC 藏青区分调性,
 * 保持品牌靛蓝主色。文本/边框取值白底对比度 ≥4.5:1(tokens.test.ts 同标准守护)。 */
export const webDarkColors = {
  bgBase: "#0F1420",
  bgContainer: "#171E30",
  bgElevated: "#1F2942",
  text: "#E5E9F2",
  textSecondary: "#9AA7C2",
  border: "#2A3552",
  /** 暗色 Menu 选中底/选中字(浅靛,深底可读) */
  menuSelectedBg: "#26304D",
  menuSelectedColor: "#A5B4FC",
} as const;

/** antd 6 ConfigProvider theme —— 用户端暗色(配合 theme.darkAlgorithm 使用);
 * 与 webTheme 同构,仅覆写底色/文本/边框系,组件级覆盖继承浅色版 */
export const webDarkTheme = {
  token: {
    ...webTheme.token,
    colorBgBase: webDarkColors.bgBase,
    colorBgContainer: webDarkColors.bgContainer,
    colorBgElevated: webDarkColors.bgElevated,
    colorBgLayout: webDarkColors.bgBase,
    colorText: webDarkColors.text,
    colorTextSecondary: webDarkColors.textSecondary,
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

