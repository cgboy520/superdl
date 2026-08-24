/**
 * 查询状态呈现约定:错误绝不伪装成数据。
 * - moneyOr:金额未就绪(加载中/失败)显示 "—",绝不渲染假 ¥0.00
 * - TableErrorEmpty:表格错误态,替代默认空态并给重试
 * - DataErrorAlert:页面级"部分数据加载失败"横幅
 */

import { Alert, Button, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";

export function moneyOr(formatted: string, ready: boolean): string {
  return ready ? formatted : "—";
}

export function TableErrorEmpty({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <Space orientation="vertical" size={8} style={{ padding: "24px 0" }}>
      <Typography.Text type="secondary">{t("query.loadFailed")}</Typography.Text>
      <Button size="small" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </Space>
  );
}

export function DataErrorAlert({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <Alert
      type="error"
      showIcon
      title={t("query.partialFailed")}
      description={t("query.partialFailedDesc")}
      action={
        <Button size="small" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      }
    />
  );
}
