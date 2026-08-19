/**
 * 查询状态呈现约定:错误绝不伪装成数据(ui-ux-spec 铁律)。
 * - moneyOr:金额未就绪(加载中/失败)显示 "—",绝不渲染假 ¥0.00
 * - TableErrorEmpty:表格错误态,替代默认空态并给重试
 * - DataErrorAlert:页面级"部分数据加载失败"横幅
 */

import { formatMoney } from "@superdl/ui";
import { Alert, Button, Space, Typography } from "antd";

export function moneyOr(value: string | null | undefined, ready: boolean): string {
  return ready ? formatMoney(value) : "—";
}

export function TableErrorEmpty({ onRetry }: { onRetry: () => void }) {
  return (
    <Space orientation="vertical" size={8} style={{ padding: "24px 0" }}>
      <Typography.Text type="secondary">
        数据加载失败,可能是网络异常或服务暂不可用
      </Typography.Text>
      <Button size="small" onClick={onRetry}>
        重 试
      </Button>
    </Space>
  );
}

export function DataErrorAlert({ onRetry }: { onRetry: () => void }) {
  return (
    <Alert
      type="error"
      showIcon
      title="部分数据加载失败"
      description="页面中的金额与列表可能不完整,请以重试后的结果为准。"
      action={
        <Button size="small" onClick={onRetry}>
          重 试
        </Button>
      }
    />
  );
}
