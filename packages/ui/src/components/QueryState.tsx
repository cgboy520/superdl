/** 查询状态呈现约定(两端共用,收编原 apps/web 的 QueryState):错误绝不伪装成数据。
 *  - moneyOr:金额未就绪(加载中/失败)显示 "—",绝不渲染假 ¥0.00
 *  - DataErrorAlert:页面级「部分数据加载失败」横幅
 *  (表格错误态用 TableErrorEmpty)
 */

import { Alert, Button } from "antd";
import { useTranslation } from "react-i18next";

export function moneyOr(formatted: string, ready: boolean): string {
  return ready ? formatted : "—";
}

export function DataErrorAlert({
  onRetry,
  title,
  description,
}: {
  onRetry: () => void;
  /** 覆盖默认「部分数据加载失败」(单查询失败场景措辞更准时用) */
  title?: string;
  description?: string;
}) {
  const { t } = useTranslation("shared");
  return (
    <Alert
      type="error"
      showIcon
      title={title ?? t("query.partialFailed")}
      description={description ?? t("query.partialFailedDesc")}
      action={
        <Button size="small" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      }
    />
  );
}
