/** user_id 单元格统一出口:点击去租户页按 id 精确找人(订单/调账/异常/实例都以 id 指代租户)。 */

import { Link } from "@tanstack/react-router";
import type { TableColumnType } from "antd";

export function TenantLink({ id }: { id: number }) {
  return <Link to="/tenants" search={{ q: String(id) }}>#{id}</Link>;
}

/** 「租户」列工厂:user_id → TenantLink(财务四表与全局实例表共用;title 由调用方给)。 */
export function tenantColumn<T extends { user_id: number }>(title: string, width = 80): TableColumnType<T> {
  return { title, dataIndex: "user_id", width, render: (v: number) => <TenantLink id={v} /> };
}
