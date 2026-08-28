/** 侧栏菜单可见性(与后端 require_roles 逐端点对齐):无权角色不显示入口,直接输 URL 由后端 403 兜底。
 *  key 收窄到 MenuKey,_app.tsx 的 MENU 新增条目而此表漏登记时编译期即报错。 */

export const ALL_ROLES = ["admin", "ops", "finance", "readonly"] as const;

export type Role = (typeof ALL_ROLES)[number];

/** 角色 → 文案键(顶栏角色 Tag 与管理员账号页共用;值在 admin.json roles.*) */
export const ROLE_LABEL_KEY = {
  admin: "roles.admin",
  ops: "roles.ops",
  finance: "roles.finance",
  readonly: "roles.readonly",
} as const satisfies Record<Role, string>;

export const MENU_ROLES = {
  "/": ALL_ROLES,
  "/nodes": ["admin", "ops", "readonly"], // finance 无 /nodes 权限
  "/cluster": ["admin", "ops", "readonly"], // 对齐后端 /cluster require_roles(finance 无)
  "/skus": ALL_ROLES,
  "/images": ["admin", "ops", "readonly"], // 对齐后端 /images require_roles(finance 无)

  "/tenants": ALL_ROLES,
  "/finance": ["admin", "finance", "readonly"], // ops 无财务权限
  "/tickets": ALL_ROLES, // 读全角色;写操作(ops/admin)由按钮级 disable + 后端 403 兜底
  "/audit": ALL_ROLES,
  "/platform": ["admin"], // 渠道凭据仅超管
  "/settings": ALL_ROLES,
} as const satisfies Record<string, readonly Role[]>;

export type MenuKey = keyof typeof MENU_ROLES;

export function canSeeMenu(key: MenuKey, role: string): boolean {
  return (MENU_ROLES[key] as readonly string[]).includes(role);
}
