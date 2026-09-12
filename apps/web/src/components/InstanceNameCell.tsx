/** 名称列:名称即详情链接;hover 出铅笔进入行内改名(Enter 保存 / Esc 取消 / blur 保存)。 */

import { EditOutlined } from "@ant-design/icons";
import { controlWidth, fontSize } from "@superdl/ui";
import { Link } from "@tanstack/react-router";
import { App, Button, Input, Space, Typography } from "antd";
import { memo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { InstanceOut } from "@superdl/api-client";
import { useRenameInstance } from "../api/mutations";

export function InstanceNameCell({ instance }: { instance: InstanceOut }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(instance.name);
  // Esc 取消标记:随后的 blur 不再保存
  const cancelRef = useRef(false);
  const rename = useRenameInstance();
  const { message } = App.useApp();
  const save = async () => {
    if (cancelRef.current) {
      cancelRef.current = false;
      return;
    }
    if (rename.isPending) return;
    const name = value.trim();
    if (!name || name === instance.name) {
      setValue(instance.name);
      setEditing(false);
      return;
    }
    try {
      await rename.mutateAsync({ uuid: instance.uuid, name });
      message.success(t("instances.renamed"));
      setEditing(false);
    } catch {
      // 错误提示由 useApiMutation 统一弹出;保持编辑态
    }
  };
  if (editing) {
    return (
      <Input
        size="small"
        autoFocus
        // 与创建页名称框同一上限(后端 64 字符)
        maxLength={64}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => void save()}
        onPressEnter={() => void save()}
        onKeyDown={(e) => {
          // Esc 恢复原值不提交
          if (e.key === "Escape") {
            cancelRef.current = true;
            setValue(instance.name);
            setEditing(false);
          }
        }}
        style={{ width: controlWidth.sm }}
      />
    );
  }
  return (
    <Space orientation="vertical" size={0} className="name-cell">
      <Space size={4} align="center">
        <Link to="/instances/$uuid" params={{ uuid: instance.uuid }} style={{ fontWeight: 600 }}>
          {instance.name}
        </Link>
        <Button
          type="text"
          size="small"
          className="row-hover-only"
          aria-label={t("instances.renameAria", { name: instance.name })}
          icon={<EditOutlined style={{ fontSize: fontSize.caption }} />}
          onClick={() => {
            cancelRef.current = false;
            setValue(instance.name);
            setEditing(true);
          }}
        />
      </Space>
      <Typography.Text type="secondary" className="mono" style={{ fontSize: fontSize.caption }}>
        {instance.uuid.slice(0, 12)}
      </Typography.Text>
    </Space>
  );
}

/** 行级 memo:仅 uuid / 名称变化才重渲染。 */
export const InstanceNameCellMemo = memo(
  InstanceNameCell,
  (prev, next) => prev.instance.uuid === next.instance.uuid && prev.instance.name === next.instance.name,
);
