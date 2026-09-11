/** 环境变量行编辑器:变量名 + 值 + 密文勾选 + 删除,行内即时报名字问题;批量粘贴 KEY=VALUE 跳过非法行并报条数。密文值创建后不回显,只能整条覆盖。 */

import { fontSize } from "@superdl/ui";
import { Button, Checkbox, Flex, Input, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  envRowIssue,
  newRowId,
  parseEnvBulk,
  type EnvNameIssue,
  type EnvRow,
} from "../../lib/serviceSpec";

export function EnvRowsEditor({
  rows,
  onChange,
}: {
  rows: EnvRow[];
  onChange: (rows: EnvRow[]) => void;
}) {
  const { t } = useTranslation();
  const [lastId, setLastId] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");
  // 最近一次批量解析跳过的条数:有跳过留在 Modal 内报数
  const [bulkSkipped, setBulkSkipped] = useState<number | null>(null);

  const issueText = (issue: EnvNameIssue): string => {
    if (issue === "invalid") return t("services.form.envNameInvalid");
    if (issue === "reserved") return t("services.form.envNameReserved");
    return t("services.form.envNameDuplicate");
  };

  const update = (id: string, patch: Partial<EnvRow>) =>
    onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const submitBulk = () => {
    const { rows: toAdd, skipped } = parseEnvBulk(
      bulkText,
      rows.map((r) => r.name.trim()).filter((n) => n !== ""),
    );
    if (toAdd.length > 0) onChange([...rows, ...toAdd]);
    setBulkText("");
    setBulkSkipped(skipped);
    if (skipped === 0) setBulkOpen(false);
  };

  return (
    <Space orientation="vertical" size={8} style={{ width: "100%" }}>
      <Typography.Text type="secondary">{t("services.form.envLabel")}</Typography.Text>
      {rows.map((row, i) => {
        const issue = envRowIssue(row, rows);
        return (
          <Space key={row.id} orientation="vertical" size={2} style={{ width: "100%" }}>
            <Flex gap={8} wrap align="center">
              <Input
                style={{ flex: "1 1 180px", minWidth: 140 }}
                placeholder={t("services.form.envNamePlaceholder")}
                aria-label={t("services.form.envNameAria", { index: i + 1 })}
                autoFocus={row.id === lastId}
                status={issue ? "error" : undefined}
                value={row.name}
                onChange={(e) => update(row.id, { name: e.target.value })}
              />
              <Input
                style={{ flex: "2 1 240px", minWidth: 180 }}
                placeholder={t("services.form.envValuePlaceholder")}
                aria-label={t("services.form.envValueAria", { index: i + 1 })}
                value={row.value}
                onChange={(e) => update(row.id, { value: e.target.value })}
              />
              <Checkbox checked={row.secret} onChange={(e) => update(row.id, { secret: e.target.checked })}>
                {t("services.form.envSecret")}
              </Checkbox>
              <Button onClick={() => onChange(rows.filter((r) => r.id !== row.id))}>
                {t("services.form.rowRemove")}
              </Button>
            </Flex>
            {issue && (
              <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
                {issueText(issue)}
              </Typography.Text>
            )}
          </Space>
        );
      })}
      <Space size={8}>
        <Button
          onClick={() => {
            const id = newRowId();
            onChange([...rows, { id, name: "", value: "", secret: false }]);
            setLastId(id);
          }}
        >
          {t("services.form.addEnv")}
        </Button>
        <Button onClick={() => setBulkOpen(true)}>{t("services.form.bulkAdd")}</Button>
      </Space>
      <Typography.Text type="secondary">{t("services.form.envSecretHint")}</Typography.Text>
      <Modal
        title={t("services.form.bulkAddEnvTitle")}
        open={bulkOpen}
        okText={t("services.form.bulkAddConfirm")}
        onOk={submitBulk}
        onCancel={() => {
          setBulkOpen(false);
          setBulkText("");
          setBulkSkipped(null);
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text type="secondary">{t("services.form.bulkAddEnvHint")}</Typography.Text>
          <Input.TextArea
            rows={8}
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"KEY=VALUE"}
            aria-label={t("services.form.bulkAddEnvTitle")}
          />
          {bulkSkipped != null && bulkSkipped > 0 && (
            <Typography.Text type="warning">
              {t("services.form.bulkAddSkipped", { count: bulkSkipped })}
            </Typography.Text>
          )}
        </Space>
      </Modal>
    </Space>
  );
}
