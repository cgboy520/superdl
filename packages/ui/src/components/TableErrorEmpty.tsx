/** 表格错误/空态(两端共用):查询失败绝不能渲染成「没有数据」——
 *  值班/用户会把故障误判为空数据,财务页误判代价最高。
 *
 *  用法(react-query):
 *    <Table
 *      loading={q.isLoading}
 *      locale={{ emptyText: (
 *        <TableErrorEmpty isError={q.isError} isForbidden={is403(q.error)} onRetry={() => void q.refetch()} />
 *      ) }}
 *    />
 *
 *  - isForbidden:403 与网络故障分开表达(无权限 ≠ 加载失败);
 *  - isError=false 时渲染空态;业务空态文案经 children、空态 CTA 经 action 传入
 *    (文案规范:空态 = 一句话 + 一个动作);
 *  - compact:表格行内紧凑形态(小字 + 小按钮),默认大 Result。
 */

import { Button, Empty, Result, Space, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function TableErrorEmpty({
  isError,
  isForbidden,
  onRetry,
  action,
  compact,
  children,
}: {
  isError: boolean;
  /** 403 无权(与故障区分);优先级高于 isError */
  isForbidden?: boolean;
  onRetry?: () => void;
  /** 非错误空态的引导动作(如「新建」按钮) */
  action?: ReactNode;
  /** 行内紧凑形态(替代大 Result) */
  compact?: boolean;
  /** 非错误时的业务空态文案(缺省 = antd 默认空态) */
  children?: ReactNode;
}) {
  const { t } = useTranslation("shared");
  if (isForbidden) {
    return (
      <Result
        status="403"
        title={t("common.forbidden")}
        subTitle={t("common.forbiddenDesc")}
      />
    );
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
      <Space orientation="vertical" size={8} style={{ padding: "24px 0" }}>
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
