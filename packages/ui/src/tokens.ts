/**
 * 两端共享设计 token(ui-ux-spec §2)。
 * 用户端:浅色,主色靛蓝(避开 antd 默认蓝与 AutoDL 品牌蓝)
 * 管理端:深色 NOC 风,亮青作数据强调、琥珀作告警
 */

export const colorPrimary = "#4F46E5";

export const adminColors = {
  bgBase: "#0B1220",
  bgElevated: "#111A2E",
  dataAccent: "#22D3EE",
  alertAccent: "#F59E0B",
} as const;

/** 状态语义色(两端同一套,管理端深色下由 antd 算法自动调亮) */
export const statusColors = {
  green: "#16A34A",
  blue: "#2563EB",
  gray: "#9CA3AF",
  orange: "#EA580C",
  red: "#DC2626",
} as const;

/** antd 6 ConfigProvider theme —— 用户端(浅色) */
export const webTheme = {
  token: {
    colorPrimary,
    colorInfo: colorPrimary,
    borderRadius: 6,
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

/** 数字列(金额/端口/利用率)统一 tabular-nums */
export const tabularNums: { fontVariantNumeric: "tabular-nums" } = {
  fontVariantNumeric: "tabular-nums",
};
