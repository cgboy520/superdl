/** user_id 单元格:点击去租户页按 id 找人。 */

import { Link } from "@tanstack/react-router";
import type { TableColumnType } from "antd";

export function TenantLink({ id }: { id: number }) {
  return <Link to="/tenants" search={{ q: String(id) }}>#{id}</Link>;
}

/** 「租户」列工厂:user_id → TenantLink。 */
export function tenantColumn<T extends { user_id: number }>(title: string, width = 80): TableColumnType<T> {
  return { title, dataIndex: "user_id", width, render: (v: number) => <TenantLink id={v} /> };
}
