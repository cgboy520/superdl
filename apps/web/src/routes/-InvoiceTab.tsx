/** 发票 Tab:申请开票(抬头 / 税号 / 邮箱校验)+ 我的发票。 */

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
import { useInvoiceEligible, useInvoicePages } from "../api/queries";

/** 发票申请弹窗:账期(仅 eligible 列表)+ 抬头信息;金额由服务端按账期计算。 */
export const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/; // 与服务端契约同一口径
// 统一社会信用代码(GB 32100-2015):18 位,数字与大写字母(不含 I/O/Z/S/V);与服务端 schemas.TAX_ID_PATTERN 同口径
export const TAX_ID_RE = /^[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}$/;

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
  // 幂等键按「提交序号 + 表单快照」派生,成功后序号 +1 即新单
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

  const emailOk = EMAIL_RE.test(email.trim());
  const taxIdOk = titleType === "personal" || TAX_ID_RE.test(taxId.trim());
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
        {/* 红框必须配文字说明(与税号字段同一标准) */}
        {title !== "" && title.trim().length < 2 ? (
          <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
            {t("billing.invoiceTitleInvalid")}
          </Typography.Text>
        ) : null}
        {titleType === "company" && (
          <>
            <Input
              value={taxId}
              onChange={(e) => setTaxId(e.target.value.toUpperCase())}
              placeholder={t("billing.invoiceTaxIdPlaceholder")}
              maxLength={18}
              status={taxId !== "" && !TAX_ID_RE.test(taxId.trim()) ? "error" : undefined}
              aria-label={t("billing.invoiceTaxId")}
            />
            {taxId !== "" && !TAX_ID_RE.test(taxId.trim()) ? (
              <Typography.Text type="danger" style={{ fontSize: fontSize.caption }}>
                {t("billing.invoiceTaxIdInvalid")}
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

/** 发票:可开票额度卡片(总额 + 各账期明细) + 申请弹窗 + 我的发票列表。 */
export function InvoiceTab() {
  const { t } = useTranslation(["web", "shared"]);
  const { formatMoney } = useFormat();
  const eligibleQ = useInvoiceEligible();
  const periods = useMemo<InvoiceEligibleOut[]>(() => eligibleQ.data ?? [], [eligibleQ.data]);
  // 总额逐账期字符串相加(2 位小数),不过 Number
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
          // 加载失败不伪装成「无可开票账期」
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
