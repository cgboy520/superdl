import { createFileRoute } from "@tanstack/react-router";
import { Result } from "antd";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  return (
    <Result
      status="info"
      title="SuperDL 管理控制台"
      subTitle="WP11 将在此实现 5 屏:总览 / 节点与 GPU / SKU 定价 / 租户实例 / 财务对账"
    />
  );
}
