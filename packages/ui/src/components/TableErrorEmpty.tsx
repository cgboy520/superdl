/** 表格的无权限、加载失败或空态展示;支持重试与自定义引导动作。 */

import { Button, Empty, Result, Space, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { space } from "../tokens";

export function TableErrorEmpty({
  isError,
  isForbidden,
  onRetry,
  action,
  compact,
  children,
}: {
  isError: boolean;
  /** 403 无权;优先级高于 isError */
  isForbidden?: boolean;
  onRetry?: () => void;
  /** 空态引导动作 */
  action?: ReactNode;
  /** 行内紧凑形态 */
  compact?: boolean;
  /** 业务空态文案(缺省 antd 默认) */
  children?: ReactNode;
}) {
  const { t } = useTranslation("shared");
  if (isForbidden) {
    return <Result status="403" title={t("common.forbidden")} subTitle={t("common.forbiddenDesc")} />;
  }
  if (!isError) {
    if (children || action) {
      return (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={children}>
          {action}
        </Empty>
      );
    }
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }
  if (compact) {
    return (
      <Space orientation="vertical" size={space.sm} style={{ padding: "24px 0" }}>
        <Typography.Text type="secondary">{t("common.loadFailed")}</Typography.Text>
        {onRetry && (
          <Button size="small" onClick={onRetry}>
            {t("common.retry")}
          </Button>
        )}
      </Space>
    );
  }
  return (
    <Result
      status="warning"
      title={t("common.loadFailed")}
      extra={
        onRetry ? (
          <Button type="primary" onClick={onRetry}>
            {t("common.retry")}
          </Button>
        ) : undefined
      }
    />
  );
}
