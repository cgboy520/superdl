/** user_id 单元格统一出口:点击去租户页按 id 精确找人(订单/调账/异常/实例都以 id 指代租户)。 */

import { Link } from "@tanstack/react-router";

export function TenantLink({ id }: { id: number }) {
  return <Link to="/tenants" search={{ q: String(id) }}>#{id}</Link>;
}
