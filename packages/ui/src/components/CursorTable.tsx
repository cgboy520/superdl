/** Cursor-paginated table with error / empty states and load more. */

import { Table } from "antd";
import type { TableProps } from "antd";
import type { ReactNode } from "react";

import { LoadMore } from "./LoadMore";
import { TableErrorEmpty } from "./TableErrorEmpty";

/** The fields CursorTable needs from a cursor query result (minimal structured surface). */
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
  /** Non-error empty copy (into antd Empty) */
  empty?: ReactNode;
  /** Custom non-error empty block (EmptyState etc.), takes precedence over empty */
  emptyNode?: ReactNode;
  /** Compact error state inside drawers / nested tables */
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
