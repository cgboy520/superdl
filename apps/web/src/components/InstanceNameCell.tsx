/** Name column: the name is the detail link; hover shows a pencil for inline rename (InlineEdit: Enter saves / Esc cancels / blur saves). */

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

/** Row-level memo: re-renders only when uuid / name change. */
export const InstanceNameCellMemo = memo(
  InstanceNameCell,
  (prev, next) => prev.instance.uuid === next.instance.uuid && prev.instance.name === next.instance.name,
);
