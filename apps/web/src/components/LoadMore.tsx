/** 游标分页「加载更多」按钮:与 useInfiniteQuery 配套(hasNextPage 才渲染)。 */

import { Button, type ButtonProps } from "antd";
import { useTranslation } from "react-i18next";

export function LoadMoreButton({
  visible,
  loading,
  onClick,
  size,
}: {
  visible: boolean;
  loading: boolean;
  onClick: () => void;
  size?: ButtonProps["size"];
}) {
  const { t } = useTranslation();
  if (!visible) return null;
  return (
    <Button block size={size} loading={loading} onClick={onClick}>
      {t("common.loadMore")}
    </Button>
  );
}
