/** 行内改名(两端统一):hover 出铅笔 → 输入框;Enter 保存 / Esc 取消 / blur 保存;保存失败保持编辑态(错误由调用方的 mutation 统一弹出)。 */

import { EditOutlined } from "@ant-design/icons";
import { Button, Input, Space } from "antd";
import { useRef, useState, type ReactNode } from "react";

import { controlWidth, iconSize, space } from "../tokens";

export function InlineEdit({
  value,
  onSave,
  ariaLabel,
  display,
  maxLength = 64,
  size = "small",
  trigger = "hover",
  width = controlWidth.sm,
  validate,
}: {
  value: string;
  onSave: (next: string) => Promise<void>;
  /** 铅笔按钮的 aria-label(含目标名) */
  ariaLabel: string;
  /** 非编辑态的显示节点(默认 = value) */
  display?: ReactNode;
  maxLength?: number;
  size?: "small" | "middle";
  /** hover = 行 hover 才显示铅笔;always = 常显 */
  trigger?: "hover" | "always";
  width?: number;
  /** 返回错误文案即拒绝保存 */
  validate?: (v: string) => string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Esc 取消标记:随后的 blur 不再保存
  const cancelRef = useRef(false);

  const save = async () => {
    if (cancelRef.current) {
      cancelRef.current = false;
      return;
    }
    if (saving) return;
    const next = draft.trim();
    if (!next || next === value) {
      setDraft(value);
      setEditing(false);
      return;
    }
    const problem = validate?.(next) ?? null;
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    try {
      await onSave(next);
      setEditing(false);
    } catch {
      // 保持编辑态
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <Input
        size={size}
        autoFocus
        // 编辑态的输入框沿用触发钮的标签,否则是无标签表单项(axe)
        aria-label={ariaLabel}
        maxLength={maxLength}
        value={draft}
        status={error ? "error" : undefined}
        disabled={saving}
        onChange={(e) => {
          setDraft(e.target.value);
          setError(null);
        }}
        onBlur={() => void save()}
        onPressEnter={() => void save()}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            cancelRef.current = true;
            setDraft(value);
            setError(null);
            setEditing(false);
          }
        }}
        style={{ width }}
      />
    );
  }
  return (
    <Space size={space.xs} align="center">
      {display ?? value}
      <Button
        type="text"
        size="small"
        className={trigger === "hover" ? "row-hover-only" : undefined}
        aria-label={ariaLabel}
        icon={<EditOutlined style={{ fontSize: iconSize.sm }} />}
        onClick={() => {
          cancelRef.current = false;
          setDraft(value);
          setError(null);
          setEditing(true);
        }}
      />
    </Space>
  );
}
