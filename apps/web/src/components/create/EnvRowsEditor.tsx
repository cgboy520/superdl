/** 环境变量行编辑器(RowsEditor 特化):变量名 + 值(密文行用密码框)+ 密文勾选;行内即时报名字问题;批量粘贴 KEY=VALUE 带预览与跳过计数。密文值创建后不回显,只能整条覆盖。 */

import { Checkbox, Input } from "antd";
import { useTranslation } from "react-i18next";

import { envRowIssue, newRowId, parseEnvBulk, type EnvNameIssue, type EnvRow } from "../../lib/serviceSpec";
import { RowsEditor } from "./RowsEditor";

export function EnvRowsEditor({
  rows,
  onChange,
  collapsible,
}: {
  rows: EnvRow[];
  onChange: (rows: EnvRow[]) => void;
  /** 更新版本抽屉里折叠为「N 个变量 · 展开编辑」 */
  collapsible?: boolean;
}) {
  const { t } = useTranslation();
  const issueText = (issue: EnvNameIssue): string => {
    if (issue === "invalid") return t("services.form.envNameInvalid");
    if (issue === "reserved") return t("services.form.envNameReserved");
    return t("services.form.envNameDuplicate");
  };
  const update = (id: string, patch: Partial<EnvRow>) =>
    onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  return (
    <RowsEditor<EnvRow>
      label={t("services.form.envLabel")}
      rows={rows}
      onChange={onChange}
      newRowId={newRowId}
      newRow={(id) => ({ id, name: "", value: "", secret: false })}
      addLabel={t("services.form.addEnv")}
      hint={t("services.form.envSecretHint")}
      collapsible={collapsible}
      rowIssue={(row) => {
        const issue = envRowIssue(row, rows);
        return issue ? issueText(issue) : null;
      }}
      renderRow={(row, i, autoFocus) => {
        const issue = envRowIssue(row, rows);
        return (
          <>
            <Input
              style={{ flex: "1 1 180px", minWidth: 140 }}
              placeholder={t("services.form.envNamePlaceholder")}
              aria-label={t("services.form.envNameAria", { index: i + 1 })}
              autoFocus={autoFocus}
              status={issue ? "error" : undefined}
              value={row.name}
              className="mono"
              onChange={(e) => update(row.id, { name: e.target.value })}
            />
            {row.secret ? (
              <Input.Password
                style={{ flex: "2 1 240px", minWidth: 180 }}
                placeholder={t("services.form.envValuePlaceholder")}
                aria-label={t("services.form.envValueAria", { index: i + 1 })}
                value={row.value}
                onChange={(e) => update(row.id, { value: e.target.value })}
              />
            ) : (
              <Input
                style={{ flex: "2 1 240px", minWidth: 180 }}
                placeholder={t("services.form.envValuePlaceholder")}
                aria-label={t("services.form.envValueAria", { index: i + 1 })}
                value={row.value}
                onChange={(e) => update(row.id, { value: e.target.value })}
              />
            )}
            <Checkbox checked={row.secret} onChange={(e) => update(row.id, { secret: e.target.checked })}>
              {t("services.form.envSecret")}
            </Checkbox>
          </>
        );
      }}
      bulk={{
        title: t("services.form.bulkAddEnvTitle"),
        hint: t("services.form.bulkAddEnvHint"),
        placeholder: "KEY=VALUE",
        parse: (text) =>
          parseEnvBulk(
            text,
            rows.map((r) => r.name.trim()).filter((n) => n !== ""),
          ),
        previewColumns: [
          { title: t("services.form.envNamePlaceholder"), render: (r) => <span className="mono">{r.name}</span> },
          { title: t("services.form.envValuePlaceholder"), render: (r) => <span className="mono">{r.value}</span> },
        ],
      }}
    />
  );
}
