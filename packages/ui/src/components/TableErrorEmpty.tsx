/** 表格错误空态(两端共用):查询失败绝不能渲染成「没有数据」——
 *  值班/用户会把故障误判为空数据,财务页误判代价最高。
 *
 *  用法(react-query):
 *    <Table
 *      loading={q.isLoading}
 *      locale={{ emptyText: <TableErrorEmpty isError={q.isError} onRetry={() => void q.refetch()} /> }}
 *    />
 *  isError=false 时渲染 antd 默认空态;有业务空态文案(如「无异常」)经 children 传入。
 */

import { Button, Empty, Result } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function TableErrorEmpty({
  isError,
  onRetry,
  children,
}: {
  isError: boolean;
  onRetry?: () => void;
  /** 非错误时的业务空态(缺省 = antd 默认空态) */
  children?: ReactNode;
}) {
  const { t } = useTranslation("shared");
  if (!isError) {
    return children ? <>{children}</> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />;
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
