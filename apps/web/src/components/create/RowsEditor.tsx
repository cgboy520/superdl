/** Row editor skeleton (shared by command arguments / environment variables): the caller renders rows (render prop).
 *  Shared behaviour: auto-focus on new rows, icon delete button, row separators, the bulk-paste Modal (parse preview
 *  and skip count, stays open when rows were skipped) and the collapsed summary "N items · expand to edit". */

import { DeleteOutlined } from "@ant-design/icons";
import { fontSize, space } from "@superdl/ui";
import { Button, Input, Modal, Space, Table, Typography, theme } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface RowsEditorProps<T extends { id: string }> {
  label: string;
  rows: T[];
  onChange: (rows: T[]) => void;
  /** Create a new (empty) row */
  newRow: (id: string) => T;
  newRowId: () => string;
  /** Row controls (without the delete button); autoFocus for the new row */
  renderRow: (row: T, index: number, autoFocus: boolean) => ReactNode;
  /** Row-level issue copy (red text under the row) */
  rowIssue?: (row: T) => string | null;
  addLabel: string;
  /** Bulk paste: parse text → new rows + skipped count; omitted = no bulk button */
  bulk?: {
    title: string;
    hint: string;
    placeholder?: string;
    parse: (text: string) => { rows: T[]; skipped: number };
    /** Preview table columns (optional): preview of the parse result */
    previewColumns?: { title: string; render: (row: T) => ReactNode }[];
  };
  /** Footer note */
  hint?: string;
  /** Collapsible: with rows and not expanded only "N items · expand to edit" is shown */
  collapsible?: boolean;
}

export function RowsEditor<T extends { id: string }>({
  label,
  rows,
  onChange,
  newRow,
  newRowId,
  renderRow,
  rowIssue,
  addLabel,
  bulk,
  hint,
  collapsible,
}: RowsEditorProps<T>) {
  const { t } = useTranslation(["web", "shared"]);
  const { token } = theme.useToken();
  const [lastId, setLastId] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");
  const [expanded, setExpanded] = useState(!collapsible);
  const preview = bulk && bulkText.trim() ? bulk.parse(bulkText) : null;

  const closeBulk = () => {
    setBulkOpen(false);
    setBulkText("");
  };
  const submitBulk = () => {
    if (!bulk || !preview) return;
    if (preview.rows.length > 0) onChange([...rows, ...preview.rows]);
    if (preview.skipped === 0) closeBulk();
    else setBulkText("");
  };

  if (collapsible && !expanded && rows.length > 0) {
    return (
      <Space size={space.sm} align="center">
        <Typography.Text type="secondary">{label}</Typography.Text>
        <Typography.Text>{t("rowsEditor.collapsed", { count: rows.length })}</Typography.Text>
        <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => setExpanded(true)}>
          {t("rowsEditor.expand")}
        </Button>
      </Space>
    );
  }

  return (
    <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
      <Typography.Text type="secondary">{label}</Typography.Text>
      {rows.map((row, i) => {
        const issue = rowIssue?.(row) ?? null;
        return (
          <div
            key={row.id}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 2,
              paddingBottom: space.sm,
              borderBottom: i < rows.length - 1 ? `1px dashed ${token.colorBorderSecondary}` : undefined,
            }}
          >
            <div style={{ display: "flex", gap: space.sm, alignItems: "center", flexWrap: "wrap" }}>
              {renderRow(row, i, row.id === lastId)}
              <Button
                type="text"
                aria-label={t("rowsEditor.removeAria", { index: i + 1 })}
                icon={<DeleteOutlined />}
                onClick={() => onChange(rows.filter((r) => r.id !== row.id))}
              />
            </div>
            {issue && (
              <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
                {issue}
              </Typography.Text>
            )}
          </div>
        );
      })}
      <Space size={space.sm}>
        <Button
          onClick={() => {
            const id = newRowId();
            onChange([...rows, newRow(id)]);
            setLastId(id);
          }}
        >
          {addLabel}
        </Button>
        {bulk && <Button onClick={() => setBulkOpen(true)}>{t("services.form.bulkAdd")}</Button>}
        {collapsible && rows.length > 0 && (
          <Button type="link" size="small" onClick={() => setExpanded(false)}>
            {t("rowsEditor.collapse")}
          </Button>
        )}
      </Space>
      {hint && (
        <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
          {hint}
        </Typography.Text>
      )}
      {bulk && (
        <Modal
          title={bulk.title}
          open={bulkOpen}
          okText={t("services.form.bulkAddConfirm")}
          okButtonProps={{ disabled: !preview || preview.rows.length === 0 }}
          onOk={submitBulk}
          onCancel={closeBulk}
        >
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            <Typography.Text type="secondary">{bulk.hint}</Typography.Text>
            <Input.TextArea
              rows={6}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder={bulk.placeholder}
              aria-label={bulk.title}
              className="mono"
            />
            {preview && (
              <>
                <Typography.Text
                  type={preview.skipped > 0 ? "warning" : "secondary"}
                  style={{ fontSize: fontSize.caption }}
                >
                  {t("rowsEditor.previewSummary", { count: preview.rows.length, skipped: preview.skipped })}
                </Typography.Text>
                {bulk.previewColumns && preview.rows.length > 0 && (
                  <Table<T>
                    size="small"
                    rowKey="id"
                    pagination={false}
                    dataSource={preview.rows}
                    columns={bulk.previewColumns.map((c, i) => ({
                      key: i,
                      title: c.title,
                      render: (_: unknown, r: T) => c.render(r),
                    }))}
                  />
                )}
              </>
            )}
          </Space>
        </Modal>
      )}
    </Space>
  );
}
