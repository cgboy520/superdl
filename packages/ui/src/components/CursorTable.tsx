/** 游标分页表格,包含错误/空态与加载更多。 */

import { Table } from "antd";
import type { TableProps } from "antd";
import type { ReactNode } from "react";

import { LoadMore } from "./LoadMore";
import { TableErrorEmpty } from "./TableErrorEmpty";

/** 游标查询结果里 CursorTable 需要的字段(结构化最小面)。 */
export interface CursorListQuery {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  isFetchNextPageError: boolean;
  fetchNextPage: () => unknown;
}

export function CursorTable<T>({
  query,
  rows,
  empty,
  emptyNode,
  compact,
  ...tableProps
}: Omit<TableProps<T>, "dataSource" | "locale" | "pagination" | "loading"> & {
  query: CursorListQuery;
  rows: T[];
  /** 非错误空态文案(进 antd Empty) */
  empty?: ReactNode;
  /** 非错误空态整块自定义(EmptyState 等),优先于 empty */
  emptyNode?: ReactNode;
  /** 抽屉 / 嵌套里的紧凑错误态 */
  compact?: boolean;
}) {
  const err = query.error;
  const isForbidden = typeof err === "object" && err !== null && "status" in err && err.status === 403;
  return (
    <>
      <Table<T>
        pagination={false}
        {...tableProps}
        loading={query.isLoading}
        dataSource={rows}
        locale={{
          emptyText:
            !query.isError && !isForbidden && emptyNode ? (
              emptyNode
            ) : (
              <TableErrorEmpty
                isError={query.isError}
                isForbidden={isForbidden}
                onRetry={() => void query.refetch()}
                compact={compact}
              >
                {empty}
              </TableErrorEmpty>
            ),
        }}
      />
      <LoadMore
        hasNextPage={query.hasNextPage}
        loading={query.isFetchingNextPage}
        isError={query.isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void query.fetchNextPage()}
      />
    </>
  );
}
