/** 游标分页「加载更多」按钮:与 useInfiniteQuery 配套(hasNextPage 才渲染)。 */

import { Button } from "antd";
import { useTranslation } from "react-i18next";

export function LoadMoreButton({
  visible,
  loading,
  onClick,
}: {
  visible: boolean;
  loading: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  if (!visible) return null;
  return (
    <Button block size="small" style={{ marginTop: 8 }} loading={loading} onClick={onClick}>
      {t("common.loadMore")}
    </Button>
  );
}
