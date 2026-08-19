/**
 * 侧栏菜单可见性(与后端 require_roles 逐端点对齐)。
 * 跨角色 403 的第一道防线:看不到入口就不会误入「空表 + ¥0.00」的假空态;
 * 直接输 URL 仍由后端 403 兜底。
 */

export const ALL_ROLES = ["admin", "ops", "finance", "readonly"] as const;

export const MENU_ROLES: Record<string, readonly string[]> = {
  "/": ALL_ROLES,
  "/nodes": ["admin", "ops", "readonly"], // finance 无 /nodes 权限
  "/skus": ALL_ROLES,
  "/tenants": ALL_ROLES,
  "/finance": ["admin", "finance", "readonly"], // ops 无财务权限
  "/audit": ALL_ROLES,
  "/platform": ["admin"], // 渠道凭据仅超管
  "/settings": ALL_ROLES,
};

export function canSeeMenu(key: string, role: string): boolean {
  return (MENU_ROLES[key] ?? []).includes(role);
}
