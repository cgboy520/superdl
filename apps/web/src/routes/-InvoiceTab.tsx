/** Invoice tab: request an invoice (title / tax id / email validation) + my invoices. */

import { useTranslation } from "react-i18next";
import { Alert, App, Button, Card, Input, Modal, Radio, Select, Space, Statistic, Tag, Typography } from "antd";
import { useMemo, useState } from "react";

import { type InvoiceEligibleOut } from "@superdl/api-client";
import {
  addAmounts,
  flattenPages,
  fontSize,
  formatDateTime,
  idemKeyOf,
  invoiceStatusMap,
  metaOf,
  space,
} from "@superdl/ui";
import { CursorTable, DataErrorAlert, moneyOr } from "@superdl/ui/components";
import { useFormat } from "@superdl/ui";

import { useCreateInvoice } from "../api/mutations";
import { useInvoiceEligible, useInvoicePages, useSiteConfig } from "../api/queries";

/** Invoice request dialog: period (eligible list only) + title information; the amount is computed server-side per period. */
export const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
/** PRC unified social credit code — only enforced client-side when the site runs the `cn` profile. */
export const CN_USCC_RE = /^[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}$/;

export function InvoiceApplyModal({
  periods,
  open,
  onClose,
}: {
  periods: InvoiceEligibleOut[];
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const [period, setPeriod] = useState<string>();
  const [titleType, setTitleType] = useState<"personal" | "company">("company");
  const [title, setTitle] = useState("");
  const [taxId, setTaxId] = useState("");
  const [email, setEmail] = useState("");
  const [submitSeq, setSubmitSeq] = useState(0);
  const create = useCreateInvoice({
    onSuccess: () => {
      message.success(t("billing.invoiceCreated"));
      setPeriod(undefined);
      setTitle("");
      setTaxId("");
      setEmail("");
      setSubmitSeq((s) => s + 1);
      onClose();
    },
  });

  const { data: site } = useSiteConfig();
  const cnTaxId = site?.compliance_profile === "cn";
  const taxIdValid = (v: string) => (cnTaxId ? CN_USCC_RE.test(v.trim()) : v.trim().length >= 2);
  const emailOk = EMAIL_RE.test(email.trim());
  const taxIdOk = titleType === "personal" || taxIdValid(taxId);
  const canSubmit = period != null && title.trim().length >= 2 && emailOk && taxIdOk;

  return (
    <Modal
      title={t("billing.invoiceApplyTitle")}
      open={open}
      onCancel={onClose}
      okText={t("billing.invoiceSubmit")}
      okButtonProps={{ disabled: !canSubmit, loading: create.isPending }}
      onOk={() => {
        if (!period || !canSubmit) return;
        create.mutate({
          body: {
            period,
            title_type: titleType,
            title: title.trim(),
            tax_id: titleType === "company" ? taxId.trim() : null,
            email: email.trim(),
          },
          idempotencyKey: idemKeyOf("invoice", [
            submitSeq,
            period,
            titleType,
            title.trim(),
            titleType === "company" ? taxId.trim() : null,
            email.trim(),
          ]),
        });
      }}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Alert type="info" showIcon title={t("billing.invoiceManualNote")} />
        <Select
          style={{ width: "100%" }}
          placeholder={t("billing.invoicePeriodSelect")}
          value={period}
          onChange={(v: string) => setPeriod(v)}
          options={periods.map((p) => ({
            value: p.period,
            label: `${p.period} · ${formatMoney(p.amount)}`,
          }))}
        />
        <Radio.Group
          optionType="button"
          value={titleType}
          onChange={(e) => setTitleType(e.target.value as "personal" | "company")}
          options={[
            { value: "company", label: t("billing.invoiceTitleTypeCompany") },
            { value: "personal", label: t("billing.invoiceTitleTypePersonal") },
          ]}
        />
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t("billing.invoiceTitlePlaceholder")}
          maxLength={128}
          status={title !== "" && title.trim().length < 2 ? "error" : undefined}
          aria-label={t("billing.invoiceTitleLabel")}
        />
        {title !== "" && title.trim().length < 2 ? (
          <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
            {t("billing.invoiceTitleInvalid")}
          </Typography.Text>
        ) : null}
        {titleType === "company" && (
          <>
            <Input
              value={taxId}
              onChange={(e) => setTaxId(cnTaxId ? e.target.value.toUpperCase() : e.target.value)}
              placeholder={cnTaxId ? t("billing.invoiceTaxIdPlaceholderCn") : t("billing.invoiceTaxIdPlaceholder")}
              maxLength={cnTaxId ? 18 : 32}
              status={taxId !== "" && !taxIdValid(taxId) ? "error" : undefined}
              aria-label={t("billing.invoiceTaxId")}
            />
            {taxId !== "" && !taxIdValid(taxId) ? (
              <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
                {cnTaxId ? t("billing.invoiceTaxIdInvalidCn") : t("billing.invoiceTaxIdInvalid")}
              </Typography.Text>
            ) : null}
          </>
        )}
        <Input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={t("billing.invoiceEmailPlaceholder")}
          maxLength={128}
          status={email !== "" && !emailOk ? "error" : undefined}
          aria-label={t("billing.invoiceEmail")}
        />
        {email !== "" && !emailOk ? (
          <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
            {t("billing.invoiceEmailInvalid")}
          </Typography.Text>
        ) : null}
      </Space>
    </Modal>
  );
}

/** Invoices: invoiceable amount card (total + per period) + request dialog + my invoice list. */
export function InvoiceTab() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const eligibleQ = useInvoiceEligible();
  const periods = useMemo<InvoiceEligibleOut[]>(() => eligibleQ.data ?? [], [eligibleQ.data]);
  const total = useMemo(() => periods.reduce((acc, p) => addAmounts(acc, p.amount), "0.00"), [periods]);
  const [applyOpen, setApplyOpen] = useState(false);
  const invoices = useInvoicePages(20);
  const rows = useMemo(() => flattenPages(invoices.data), [invoices.data]);

  return (
    <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
      <Card size="small">
        <Space style={{ width: "100%", justifyContent: "space-between" }} align="start" wrap>
          <Statistic
            title={t("billing.invoiceEligibleTotal")}
            value={moneyOr(formatMoney(total), eligibleQ.data != null)}
          />
          <Button type="primary" disabled={periods.length === 0} onClick={() => setApplyOpen(true)}>
            {t("billing.invoiceApply")}
          </Button>
        </Space>
        {eligibleQ.isError ? (
          <DataErrorAlert onRetry={() => void eligibleQ.refetch()} />
        ) : periods.length > 0 ? (
          <Space wrap size={space.sm} style={{ marginTop: 12 }}>
            {periods.map((p) => (
              <Tag key={p.period}>{`${p.period} · ${formatMoney(p.amount)}`}</Tag>
            ))}
          </Space>
        ) : (
          !eligibleQ.isLoading && (
            <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>
              {t("billing.invoiceNoEligible")}
            </Typography.Text>
          )
        )}
        <Typography.Text type="secondary" style={{ display: "block", marginTop: 8, fontSize: fontSize.caption }}>
          {t("billing.invoiceEligibleHint")}
          {" · "}
          {t("billing.invoiceManualNote")}
        </Typography.Text>
      </Card>
      <CursorTable
        query={invoices}
        rows={rows}
        empty={t("billing.invoiceNone")}
        rowKey="id"
        size="small"
        scroll={{ x: 860 }}
        columns={[
          { title: t("billing.colPeriod"), dataIndex: "period" },
          {
            title: t("billing.colAmount"),
            render: (_, r) => <span>{formatMoney(r.amount)}</span>,
          },
          { title: t("billing.colTitle"), dataIndex: "title", ellipsis: true },
          {
            title: t("billing.colStatus"),
            render: (_, r) => {
              const m = metaOf(invoiceStatusMap, r.status);
              return <Tag color={m?.color}>{m ? t(m.labelKey) : r.status}</Tag>;
            },
          },
          {
            title: t("billing.colInvoiceNo"),
            render: (_, r) => r.invoice_no ?? "—",
          },
          {
            title: t("billing.colRejectReason"),
            render: (_, r) => (r.status === "rejected" ? (r.reject_reason ?? "—") : "—"),
          },
          { title: t("billing.colTime"), dataIndex: "created_at", render: formatDateTime },
        ]}
      />
      <InvoiceApplyModal periods={periods} open={applyOpen} onClose={() => setApplyOpen(false)} />
    </Space>
  );
}
