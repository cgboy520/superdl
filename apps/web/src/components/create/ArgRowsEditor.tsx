/** 启动参数行编辑器:一行一个参数(带空格的参数不会被拆开)+ 批量粘贴;新增行自动聚焦。 */

import { Button, Flex, Input, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { newRowId, parseArgBulk, type ArgRow } from "../../lib/serviceSpec";

export function ArgRowsEditor({
  rows,
  onChange,
}: {
  rows: ArgRow[];
  onChange: (rows: ArgRow[]) => void;
}) {
  const { t } = useTranslation();
  const [lastId, setLastId] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");

  const submitBulk = () => {
    const toAdd = parseArgBulk(bulkText);
    if (toAdd.length > 0) onChange([...rows, ...toAdd]);
    setBulkText("");
    setBulkOpen(false);
  };

  return (
    <Space orientation="vertical" size={8} style={{ width: "100%" }}>
      <Typography.Text type="secondary">{t("create.argsLabel")}</Typography.Text>
      {rows.map((row, i) => (
        <Flex key={row.id} gap={8} wrap style={{ width: "100%" }}>
          <Input
            style={{ flex: "1 1 320px", minWidth: 0 }}
            placeholder={t("create.argPlaceholder")}
            aria-label={t("create.argAria", { index: i + 1 })}
            autoFocus={row.id === lastId}
            value={row.value}
            onChange={(e) =>
              onChange(rows.map((r) => (r.id === row.id ? { ...r, value: e.target.value } : r)))
            }
          />
          <Button onClick={() => onChange(rows.filter((r) => r.id !== row.id))}>
            {t("create.rowRemove")}
          </Button>
        </Flex>
      ))}
      <Space size={8}>
        <Button
          onClick={() => {
            const id = newRowId();
            onChange([...rows, { id, value: "" }]);
            setLastId(id);
          }}
        >
          {t("create.addArg")}
        </Button>
        <Button onClick={() => setBulkOpen(true)}>{t("create.bulkAdd")}</Button>
      </Space>
      <Modal
        title={t("create.bulkAddArgsTitle")}
        open={bulkOpen}
        okText={t("create.bulkAddConfirm")}
        onOk={submitBulk}
        onCancel={() => {
          setBulkOpen(false);
          setBulkText("");
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text type="secondary">{t("create.bulkAddArgsHint")}</Typography.Text>
          <Input.TextArea
            rows={8}
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            aria-label={t("create.bulkAddArgsTitle")}
          />
        </Space>
      </Modal>
    </Space>
  );
}
