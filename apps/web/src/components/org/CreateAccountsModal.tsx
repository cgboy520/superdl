/** Tenant admins create accounts named <tenant code>.<name>: one at a time or in a batch (a list, or a numbered sequence), each with a random password or one shared password. The passwords are shown once, with a CSV download; the accounts set their own at first sign-in and need no identity verification. */

import type { ManagedUserCreatedOut } from "@superdl/api-client";
import { csvOf, fontSize, saveCsv, space } from "@superdl/ui";
import { CopyField } from "@superdl/ui/components";
import { Alert, App, Button, Input, InputNumber, Modal, Segmented, Space, Table, Typography } from "antd";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useCreateTenantUsers } from "../../api/mutations";
import { Field } from "../Field";

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const MAX_BATCH = 100;
const MIN_PASSWORD = 12;

type Mode = "single" | "list" | "sequence";
type PasswordMode = "random" | "shared";

/** Names of a numbered sequence: prefix + zero-padded numbers (student01 … student30). */
export function sequenceNames(prefix: string, start: number, count: number, width: number): string[] {
  return Array.from({ length: Math.max(0, count) }, (_, i) => `${prefix}${String(start + i).padStart(width, "0")}`);
}

/** Names typed one per line, or separated by commas or spaces; lowercased, blanks dropped. */
export function listNames(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((n) => n.trim().toLowerCase())
    .filter(Boolean);
}

export function credentialsCsv(rows: { username: string; password: string }[]): string {
  return csvOf([["username", "password"], ...rows.map((r) => [r.username, r.password])]);
}

/** One-time credentials: table + copy + CSV, with the warning that they are not shown again. */
export function CredentialsResult({
  rows,
  fileName,
}: {
  rows: { username: string; password: string }[];
  fileName: string;
}) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState(false);
  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <Alert type="warning" showIcon title={t("org.accounts.passwordsOnce")} />
      <Space>
        <Button
          type="primary"
          onClick={() => {
            saveCsv(fileName, credentialsCsv(rows));
            setSaved(true);
          }}
        >
          {t("org.accounts.downloadCsv")}
        </Button>
        {saved && <Typography.Text type="success">{t("org.accounts.downloaded")}</Typography.Text>}
      </Space>
      <Table
        rowKey="username"
        size="small"
        pagination={rows.length > 10 ? { pageSize: 10 } : false}
        dataSource={rows}
        columns={[
          { title: t("org.accounts.colUsername"), render: (_, r) => <CopyField value={r.username} code /> },
          { title: t("org.accounts.colPassword"), render: (_, r) => <CopyField value={r.password} code secret /> },
        ]}
      />
    </Space>
  );
}

export function CreateAccountsModal({ open, code, onClose }: { open: boolean; code: string; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [mode, setMode] = useState<Mode>("single");
  const [single, setSingle] = useState("");
  const [list, setList] = useState("");
  const [prefix, setPrefix] = useState("user");
  const [start, setStart] = useState(1);
  const [count, setCount] = useState(10);
  const [width, setWidth] = useState(2);
  const [passwordMode, setPasswordMode] = useState<PasswordMode>("random");
  const [password, setPassword] = useState("");
  const [remark, setRemark] = useState("");
  const [created, setCreated] = useState<ManagedUserCreatedOut[] | null>(null);
  const create = useCreateTenantUsers({
    onSuccess: (rows) => {
      setCreated(rows);
      message.success(t("org.accounts.created", { n: rows.length }));
    },
  });

  const names = useMemo(() => {
    if (mode === "single") return single.trim() ? [single.trim().toLowerCase()] : [];
    if (mode === "list") return listNames(list);
    return sequenceNames(prefix.trim().toLowerCase(), start, count, width);
  }, [mode, single, list, prefix, start, count, width]);
  const invalid = names.filter((n) => !NAME_RE.test(n));
  const duplicates = names.filter((n, i) => names.indexOf(n) !== i);
  const nameError =
    names.length > MAX_BATCH
      ? t("org.accounts.tooMany", { max: MAX_BATCH })
      : invalid.length > 0
        ? t("org.accounts.namesInvalid", { names: invalid.slice(0, 3).join(", ") })
        : duplicates.length > 0
          ? t("org.accounts.namesDuplicate", { names: [...new Set(duplicates)].slice(0, 3).join(", ") })
          : null;
  const passwordError =
    passwordMode === "shared" && password.length > 0 && password.length < MIN_PASSWORD
      ? t("org.accounts.passwordShort", { min: MIN_PASSWORD })
      : null;
  const ready =
    names.length > 0 &&
    !nameError &&
    (passwordMode === "random" || (password.length >= MIN_PASSWORD && !passwordError));

  const close = () => {
    setCreated(null);
    setSingle("");
    setList("");
    setPassword("");
    onClose();
  };
  const submit = () =>
    create.mutate({
      names,
      password: passwordMode === "shared" ? password : null,
      remark: remark.trim() || null,
    });

  if (created) {
    return (
      <Modal
        title={t("org.accounts.resultTitle", { n: created.length })}
        open={open}
        onCancel={close}
        footer={<Button onClick={close}>{t("org.accounts.done")}</Button>}
        width={720}
        mask={{ closable: false }}
      >
        <CredentialsResult rows={created} fileName={`${code}-accounts.csv`} />
      </Modal>
    );
  }
  return (
    <Modal
      title={t("org.accounts.createTitle")}
      open={open}
      onCancel={close}
      onOk={submit}
      okText={t("org.accounts.createSubmit", { n: names.length })}
      okButtonProps={{ disabled: !ready }}
      confirmLoading={create.isPending}
      width={640}
      destroyOnHidden
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("org.accounts.createHint", { code })}</Typography.Text>
        <Segmented<Mode>
          value={mode}
          onChange={setMode}
          options={[
            { value: "single", label: t("org.accounts.modeSingle") },
            { value: "list", label: t("org.accounts.modeList") },
            { value: "sequence", label: t("org.accounts.modeSequence") },
          ]}
        />
        {mode === "single" && (
          <Field label={t("org.accounts.username")} required error={nameError}>
            <Space.Compact block>
              <Space.Addon>
                <span className="mono">{`${code}.`}</span>
              </Space.Addon>
              <Input
                maxLength={32}
                value={single}
                onChange={(e) => setSingle(e.target.value)}
                aria-label={t("org.accounts.username")}
                className="mono"
              />
            </Space.Compact>
          </Field>
        )}
        {mode === "list" && (
          <Field
            label={t("org.accounts.names")}
            required
            error={nameError}
            hint={t("org.accounts.namesHint", { code })}
          >
            <Input.TextArea
              rows={6}
              value={list}
              onChange={(e) => setList(e.target.value)}
              aria-label={t("org.accounts.names")}
              className="mono"
            />
          </Field>
        )}
        {mode === "sequence" && (
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            <Space size={space.md} wrap>
              <Field label={t("org.accounts.prefix")} required>
                <Input
                  maxLength={28}
                  value={prefix}
                  onChange={(e) => setPrefix(e.target.value)}
                  aria-label={t("org.accounts.prefix")}
                  style={{ width: 160 }}
                  className="mono"
                />
              </Field>
              <Field label={t("org.accounts.start")}>
                <InputNumber
                  min={0}
                  max={99999}
                  value={start}
                  onChange={(v) => setStart(v ?? 0)}
                  aria-label={t("org.accounts.start")}
                />
              </Field>
              <Field label={t("org.accounts.count")}>
                <InputNumber
                  min={1}
                  max={MAX_BATCH}
                  value={count}
                  onChange={(v) => setCount(v ?? 1)}
                  aria-label={t("org.accounts.count")}
                />
              </Field>
              <Field label={t("org.accounts.digits")}>
                <InputNumber
                  min={1}
                  max={5}
                  value={width}
                  onChange={(v) => setWidth(v ?? 1)}
                  aria-label={t("org.accounts.digits")}
                />
              </Field>
            </Space>
            {nameError ? (
              <Typography.Text type="danger">{nameError}</Typography.Text>
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }} className="mono">
                {names.length > 0 &&
                  t("org.accounts.sequencePreview", {
                    first: `${code}.${names[0] ?? ""}`,
                    last: `${code}.${names[names.length - 1] ?? ""}`,
                    n: names.length,
                  })}
              </Typography.Text>
            )}
          </Space>
        )}
        <Field label={t("org.accounts.passwordLabel")}>
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            <Segmented<PasswordMode>
              value={passwordMode}
              onChange={setPasswordMode}
              options={[
                { value: "random", label: t("org.accounts.passwordRandom") },
                { value: "shared", label: t("org.accounts.passwordShared") },
              ]}
            />
            {passwordMode === "shared" && (
              <Input.Password
                value={password}
                maxLength={128}
                onChange={(e) => setPassword(e.target.value)}
                aria-label={t("org.accounts.passwordShared")}
                status={passwordError ? "error" : undefined}
                autoComplete="new-password"
              />
            )}
            {passwordError && <Typography.Text type="danger">{passwordError}</Typography.Text>}
            <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
              {t("org.accounts.passwordNote")}
            </Typography.Text>
          </Space>
        </Field>
        <Field label={t("org.members.remark")} hint={t("org.accounts.remarkHint")}>
          <Input
            value={remark}
            maxLength={64}
            onChange={(e) => setRemark(e.target.value)}
            aria-label={t("org.members.remark")}
          />
        </Field>
      </Space>
    </Modal>
  );
}
