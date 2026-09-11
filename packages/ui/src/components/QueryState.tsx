/** 查询状态呈现(两端共用):moneyOr 金额未就绪显示 "—";DataErrorAlert 页面级「部分数据加载失败」横幅。 */

import { Alert, Button } from "antd";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";

export function moneyOr(formatted: string, ready: boolean): string {
  return ready ? formatted : "—";
}

export function DataErrorAlert({
  onRetry,
  title,
  description,
  style,
}: {
  onRetry: () => void;
  /** 覆盖默认文案 */
  title?: string;
  /** null = 不出描述行(单行错误条) */
  description?: string | null;
  style?: CSSProperties;
}) {
  const { t } = useTranslation("shared");
  return (
    <Alert
      type="error"
      showIcon
      style={style}
      title={title ?? t("query.partialFailed")}
      description={description === null ? undefined : (description ?? t("query.partialFailedDesc"))}
      action={
        <Button size="small" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      }
    />
  );
}
