/** 游标分页「加载更多」(两端共用,收编两端各自 LoadMoreButton):
 *  - hasNextPage → 加载按钮;isFetchNextPageError → 错误提示 + 重试(原实现失败静默);
 *  - 无下一页且已有数据 → 「已加载全部 N 条」收尾态(原实现直接消失,用户不知是否还有数据)。
 */

import { Alert, Button, Typography } from "antd";
import { useTranslation } from "react-i18next";

export function LoadMore({
  hasNextPage,
  loading,
  isError,
  loadedCount,
  onLoadMore,
}: {
  hasNextPage: boolean;
  loading: boolean;
  /** fetchNextPage 失败(给重试入口,不再静默) */
  isError?: boolean;
  /** 已加载条数(收尾态文案);0 时不渲染收尾 */
  loadedCount?: number;
  onLoadMore: () => void;
}) {
  const { t } = useTranslation("shared");
  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        style={{ marginTop: 8 }}
        title={t("common.loadFailed")}
        action={
          <Button size="small" onClick={onLoadMore}>
            {t("common.retry")}
          </Button>
        }
      />
    );
  }
  if (hasNextPage) {
    return (
      <Button block size="small" style={{ marginTop: 8 }} loading={loading} onClick={onLoadMore}>
        {t("common.loadMore")}
      </Button>
    );
  }
  if (loadedCount) {
    return (
      <Typography.Text
        type="secondary"
        style={{ display: "block", textAlign: "center", padding: "8px 0" }}
      >
        {t("common.loadedAll", { count: loadedCount })}
      </Typography.Text>
    );
  }
  return null;
}
