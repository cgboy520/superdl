/** user_id cell: click goes to the tenant page, finds the tenant by id and opens the drawer directly (?tenant=). */

import { Link } from "@tanstack/react-router";
import type { TableColumnType } from "antd";

export function TenantLink({ id }: { id: number }) {
  return (
    <Link to="/tenants" search={{ q: String(id), tenant: id }}>
      #{id}
    </Link>
  );
}

/** "Tenant" column factory: user_id → TenantLink. */
export function tenantColumn<T extends { user_id: number }>(title: string, width = 80): TableColumnType<T> {
  return { title, dataIndex: "user_id", width, render: (v: number) => <TenantLink id={v} /> };
}
