/** Cursor pagination "load more" (shared by both consoles): hasNextPage → button; isFetchNextPageError → retry; no next page → "all N loaded". */

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
  /** fetchNextPage failed */
  isError?: boolean;
  /** Loaded count (end-state copy); nothing rendered at 0 */
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
      <Typography.Text type="secondary" style={{ display: "block", textAlign: "center", padding: "8px 0" }}>
        {t("common.loadedAll", { count: loadedCount })}
      </Typography.Text>
    );
  }
  return null;
}
