/** Inline rename (shared by both consoles): hover shows the pencil → input; Enter saves / Esc cancels / blur saves; a failed save stays in edit mode (the error is shown by the caller's mutation). */

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
  /** aria-label of the pencil button (with the target name) */
  ariaLabel: string;
  /** Display node when not editing (default = value) */
  display?: ReactNode;
  maxLength?: number;
  size?: "small" | "middle";
  /** hover = the pencil shows only on row hover; always = always shown */
  trigger?: "hover" | "always";
  width?: number;
  /** Returning error copy rejects the save */
  validate?: (v: string) => string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
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
      /* ignored */
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <Input
        size={size}
        autoFocus
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
