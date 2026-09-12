/** 启动参数行编辑器(RowsEditor 特化):一行一个参数(带空格的参数不会被拆开)+ 批量粘贴带预览。 */

import { Input } from "antd";
import { useTranslation } from "react-i18next";

import { newRowId, parseArgBulk, type ArgRow } from "../../lib/serviceSpec";
import { RowsEditor } from "./RowsEditor";

export function ArgRowsEditor({ rows, onChange }: { rows: ArgRow[]; onChange: (rows: ArgRow[]) => void }) {
  const { t } = useTranslation();
  return (
    <RowsEditor<ArgRow>
      label={t("services.form.argsLabel")}
      rows={rows}
      onChange={onChange}
      newRowId={newRowId}
      newRow={(id) => ({ id, value: "" })}
      addLabel={t("services.form.addArg")}
      renderRow={(row, i, autoFocus) => (
        <Input
          style={{ flex: "1 1 320px", minWidth: 0 }}
          placeholder={t("services.form.argPlaceholder")}
          aria-label={t("services.form.argAria", { index: i + 1 })}
          autoFocus={autoFocus}
          value={row.value}
          className="mono"
          onChange={(e) => onChange(rows.map((r) => (r.id === row.id ? { ...r, value: e.target.value } : r)))}
        />
      )}
      bulk={{
        title: t("services.form.bulkAddArgsTitle"),
        hint: t("services.form.bulkAddArgsHint"),
        parse: (text) => ({ rows: parseArgBulk(text), skipped: 0 }),
        previewColumns: [
          { title: t("services.form.argsLabel"), render: (r) => <span className="mono">{r.value}</span> },
        ],
      }}
    />
  );
}
