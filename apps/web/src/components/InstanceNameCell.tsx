/** 名称列:名称即详情链接;hover 出铅笔进入行内改名(InlineEdit:Enter 保存 / Esc 取消 / blur 保存)。 */

import { fontSize } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { InlineEdit, Mono } from "@superdl/ui/components";
import { App, Space, Typography } from "antd";
import { memo } from "react";
import { useTranslation } from "react-i18next";

import type { InstanceOut } from "@superdl/api-client";
import { useRenameInstance } from "../api/mutations";

export function InstanceNameCell({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const rename = useRenameInstance();
  const { message } = App.useApp();
  return (
    <Space orientation="vertical" size={0} className="name-cell">
      <InlineEdit
        value={instance.name}
        ariaLabel={t("instances.renameAria", { name: instance.name })}
        display={
          <Link to="/instances/$uuid" params={{ uuid: instance.uuid }} style={{ fontWeight: 600 }}>
            {instance.name}
          </Link>
        }
        onSave={async (next) => {
          // 错误提示由 useApiMutation 统一弹出;InlineEdit 失败保持编辑态
          await rename.mutateAsync({ uuid: instance.uuid, name: next });
          message.success(t("instances.renamed"));
        }}
      />
      <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
        <Mono truncate={12}>{instance.uuid}</Mono>
      </Typography.Text>
    </Space>
  );
}

/** 行级 memo:仅 uuid / 名称变化才重渲染。 */
export const InstanceNameCellMemo = memo(
  InstanceNameCell,
  (prev, next) => prev.instance.uuid === next.instance.uuid && prev.instance.name === next.instance.name,
);
