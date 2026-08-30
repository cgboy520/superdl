/**
 * 两端共享设计 token。
 * 用户端:浅色,主色靛蓝,顶栏/Hero 用 brand 渐变常量。
 * 管理端:深色 NOC 风,亮青作数据强调、琥珀作告警。
 *
 * 【同步清单】改动底色类 token 时须同步以下防 FOUC / 硬编码位置:
 *  - brand.pageBg        → apps/web/index.html 内联脚本底色、apps/web/src/routes/__root.tsx
 *  - webDarkColors.bgBase → apps/web/index.html 内联脚本底色
 *  - adminColors.bgBase   → apps/admin/index.html 静态底色(改后同步 admin main.tsx 注入的 CSS 变量)
 */

export const colorPrimary = "#4F46E5";

/** 系统字体栈;数字对齐靠全局 tabular-nums(见 apps/web/src/styles.css) */
export const fontFamily =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", ' +
  '"Microsoft YaHei", "Helvetica Neue", Arial, sans-serif';

/** 品牌渐变端色(单一事实源:topBar/hero 渐变串与 BrandLogo SVG stop 均由此派生) */
export const brandGradientStops = {
  topBarFrom: "#4338CA",
  heroFrom: "#312E81",
  heroMid: "#4F46E5",
  heroTo: "#6D28D9",
} as const;

/** 品牌渐变与品牌面(用户端公开层 + 控制台顶栏) */
export const brand = {
  /** 控制台/公开页全宽顶栏底 */
  topBarBg: `linear-gradient(90deg, ${brandGradientStops.topBarFrom} 0%, ${brandGradientStops.heroMid} 100%)`,
  /** 主页 Hero / 登录页左栏底 */
  heroBg: `linear-gradient(135deg, ${brandGradientStops.heroFrom} 0%, ${brandGradientStops.heroMid} 55%, ${brandGradientStops.heroTo} 100%)`,
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
  /** 侧栏菜单选中底(深靛,与 dataAccent 选中字配对) */
  menuSelectedBg: "#22355E",
} as const;

/** 实心底徽标/热力格上的文字色(深底白字约定,白字对比度由 tokens.test.ts 回归守护) */
export const textOnAccent = "#FFFFFF";

/** 状态语义色(两端同一套,管理端深色下由 antd 算法自动调亮)。
 * 徽标为「深底白字」:全部取值白字对比度 ≥4.5:1(WCAG AA,tokens.test.ts 回归守护)。 */
export const statusColors = {
  green: "#15803D",
  blue: "#2563EB",
  gray: "#6B7280",
  orange: "#C2410C",
  red: "#DC2626",
  /** 调账等第五类语义(紫;白字 ≥4.5:1) */
  purple: "#6D28D9",
} as const;

/** 图表强调色(noc 主题调色板与落地页算力排名条共用,单一事实源) */
export const chartAccentColors = {
  indigo: "#818CF8",
  pink: "#F472B6",
} as const;

/** 图表系列色(绿/橙/中性,web-light 与 web-dark 两套 EChart 主题调色板的唯一事实源;
 *  与 statusColors 同族但取更亮档适配细线/小面积;非文本图形 ≥3:1 由 tokens.test.ts 守护) */
export const chartSeriesColors = {
  light: { green: "#16A34A", orange: "#EA580C", neutral: "#64748B" },
  dark: { green: "#4ADE80", orange: "#FB923C", neutral: "#94A3B8" },
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

/** 管理端组件级覆写(与 adminThemeToken 搭配传入 ConfigProvider):darkAlgorithm 出厂值的
 *  「默认深色感」主要在这几处——表头底、菜单选中、卡片圆角、Statistic 层级。 */
export const adminThemeComponents = {
  Table: {
    headerBg: adminColors.gridLine,
    headerColor: adminColors.textSecondary,
    cellPaddingBlock: 12,
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

/** 间距阶梯(4 的倍数)。纪律:新代码的布局尺寸(padding/gap/margin)一律走这里,
 * 不再散落魔法数;存量 inline style 按「碰到的文件顺手收敛」推进(见 docs/ui-ux-spec.md) */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

/** 阴影阶梯。纪律:内容卡默认 1px 边框 + 无阴影;阴影只给「浮起」语义——
 *  可点击卡 hover(sm)、sticky 浮层(upMd,向上)、Modal 级强调(lg,基本用不到)。
 *  浅色取低透明黑影;深底上浅阴影不可见,暗色/管理端用更深黑影。 */
export const shadow = {
  light: {
    sm: "0 1px 2px rgba(15, 20, 32, 0.06), 0 2px 8px rgba(15, 20, 32, 0.04)",
    md: "0 4px 12px rgba(15, 20, 32, 0.08)",
    lg: "0 8px 24px rgba(15, 20, 32, 0.12)",
    /** sticky 底栏向上投影(CheckoutBar/批量操作栏) */
    upMd: "0 -4px 12px rgba(15, 20, 32, 0.06)",
  },
  dark: {
    sm: "0 1px 2px rgba(0, 0, 0, 0.4), 0 2px 8px rgba(0, 0, 0, 0.3)",
    md: "0 4px 12px rgba(0, 0, 0, 0.45)",
    lg: "0 8px 24px rgba(0, 0, 0, 0.5)",
    upMd: "0 -4px 12px rgba(0, 0, 0, 0.45)",
  },
} as const;

/** 字号层级:页面标题/区块标题/正文/辅助/KPI 五档(散落硬编码的收敛目标) */
export const fontSize = {
  pageTitle: 20,
  sectionTitle: 16,
  body: 14,
  caption: 12,
  kpi: 28,
} as const;

/** 字重阶梯(收编散落 500/600/700):正文 regular、按钮与强调 medium、标题与 KPI 大数 semibold */
export const fontWeight = { regular: 400, medium: 500, semibold: 600 } as const;

/** 行高:与 fontSize 五档一一配对(标题紧凑、正文透气) */
export const lineHeight = {
  pageTitle: 28,
  sectionTitle: 24,
  body: 22,
  caption: 18,
  kpi: 36,
} as const;

/** 断点(与 antd Grid 同值):CSS 媒体查询的唯一事实源,styles.css 里的裸像素断点一律改走这里 */
export const breakpoint = { xs: 480, sm: 576, md: 768, lg: 992, xl: 1200 } as const;

/** 版式常量:页容器与卡片网格。页宽三档:控制台 / 落地页各 section / 法务·帮助等长文页 */
export const layout = {
  pageMaxWidth: 1280,
  pageMaxWidthWide: 1200,
  pageMaxWidthNarrow: 880,
  contentPadding: 24,
  cardGap: 16,
  cardRadius: 10,
  /** 落地页 section 纵向留白 */
  sectionPaddingY: 48,
} as const;

/** 动效常量(克制三档;与 MotionConfig reducedMotion="user" 配合,装饰性动效为零)。
 *  用法见 docs/ui-ux-spec.md §2:仅透明度/位移,禁弹性过冲;路由切换不动效。 */
export const motion = {
  /** 状态变更淡入(通知条目/结算摘要变更) */
  fast: 0.15,
  /** 常规过渡(徽标变色/浮层) */
  normal: 0.2,
  /** 强调过渡(KPI 数字滑动) */
  slow: 0.25,
  /** 统一缓出曲线 */
  easeOut: [0.16, 1, 0.3, 1],
} as const;

/** 层叠常量:自绘浮层(结算条/回到底部钮/命令面板)统一走这里,禁散落 zIndex 魔法数。
 *  antd 组件层(Modal 1000/Popover 1030)不覆写。 */
export const zIndex = {
  stickyBar: 50,
  floatingButton: 60,
  commandPalette: 80,
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
    // 描述文本不走 darkAlgorithm 派生(派生值无对比度守护):显式取已回归的次级文本色
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

/** CSS 变量桥:token 的 :root 注入值(非 antd 覆盖区——CSS 文件/滚动条/focus 描边——的唯一事实源)。
 *  由 apps/web/src/routes/__root.tsx 随主题注入;styles.css 只引用 var(),不再硬编码 hex。
 *  暗色主色提浅(menuSelectedColor 同源):深底上 #4F46E5 白字对比度不足,描边/选中态用 #A5B4FC。 */
export const cssVars = {
  light: {
    "--sdl-color-primary": colorPrimary,
    "--sdl-color-bg": brand.pageBg,
    "--sdl-color-text": "rgba(0,0,0,0.88)",
    "--sdl-scroll-thumb": "rgba(0,0,0,0.25)",
  },
  dark: {
    "--sdl-color-primary": webDarkColors.menuSelectedColor,
    "--sdl-color-bg": webDarkColors.bgBase,
    "--sdl-color-text": webDarkColors.text,
    "--sdl-scroll-thumb": "rgba(255,255,255,0.25)",
  },
} as const;

