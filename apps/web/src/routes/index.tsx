import { createFileRoute } from "@tanstack/react-router";
import { Result } from "antd";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  return (
    <Result
      status="info"
      title="SuperDL 用户控制台"
      subTitle="WP10 将在此实现 6 屏:市场 / 创建 / 实例 / 详情 / 费用 / 存储"
    />
  );
}
