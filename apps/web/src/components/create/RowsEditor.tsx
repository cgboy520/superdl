/** 行编辑器骨架(启动参数 / 环境变量共用):行渲染由调用方给(render prop),这里统一「新增行自动聚焦 / 图标删除钮 / 行分隔 / 批量粘贴 Modal(解析预览 + 跳过计数,有跳过留在 Modal)/ 可折叠为「N 项 · 展开编辑」」。 */

import { DeleteOutlined } from "@ant-design/icons";
import { fontSize, space } from "@superdl/ui";
import { Button, Input, Modal, Space, Table, Typography, theme } from "antd";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export interface RowsEditorProps<T extends { id: string }> {
  label: string;
  rows: T[];
  onChange: (rows: T[]) => void;
  /** 新建一行(空值) */
  newRow: (id: string) => T;
  newRowId: () => string;
  /** 行内控件(不含删除钮);autoFocus 为新增行 */
  renderRow: (row: T, index: number, autoFocus: boolean) => ReactNode;
  /** 行级问题文案(红字在行下) */
  rowIssue?: (row: T) => string | null;
  addLabel: string;
  /** 批量粘贴:解析文本 → 新行 + 跳过条数;不传则不出批量按钮 */
  bulk?: {
    title: string;
    hint: string;
    placeholder?: string;
    parse: (text: string) => { rows: T[]; skipped: number };
    /** 预览表列(可选):预览解析结果 */
    previewColumns?: { title: string; render: (row: T) => ReactNode }[];
  };
  /** 底部说明 */
  hint?: string;
  /** 可折叠:有行且未展开时只显示「N 项 · 展开编辑」 */
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
    // 有跳过留在 Modal 里看预览,全部通过才关
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
