/** 发票 Tab:开具 / 驳回。 */

import { useQueryClient } from "@tanstack/react-query";
import { Button, Form, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { adminColors, fontSize, formatDateTime, invoiceStatusMap, layout } from "@superdl/ui";
import { TableErrorEmpty } from "@superdl/ui/components";
import { useCsvExport } from "@superdl/ui";
import { useFormat } from "@superdl/ui";

import {
  type InvoiceRow,
  exportInvoicesCsv,
  isApiError,
  useInvoices,
  useIssueInvoice,
  useRejectInvoice,
} from "../../api";
import { LIST_CAPS, ListCapNote } from "../../components/ListCapNote";
import { ReasonAction } from "../../components/ReasonAction";
import { StatusTag } from "../../components/StatusTag";
import { isValidReason, REASON_MAX_LEN } from "../../lib/validators";
import { RowActionModal } from "../../components/RowActionModal";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteFinance, useAdminRole } from "../../stores/auth";
import { PERIOD_RE, useFinanceFilters } from "./-financeFilters";

/** 开票弹窗:填发票号;提交即站内信通知。 */
export function IssueInvoiceModal({
  target,
  onClose,
  onDone,
}: {
  target: InvoiceRow | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const [form] = Form.useForm<{ invoice_no: string }>();
  const issue = useIssueInvoice();
  if (!target) return null;
  return (
    <RowActionModal
      title={t("finance.invoiceIssueTitle")}
      okText={t("finance.invoiceIssue")}
      note={t("finance.invoiceIssueNote", {
        period: target.period,
        amount: formatMoney(target.amount),
        title: target.title,
        email: target.email,
      })}
      form={form}
      submit={(values) => issue.mutateAsync({ invoiceId: target.id, data: values })}
      successText={t("finance.invoiceIssued")}
      failText={t("finance.invoiceIssueFailed")}
      onClose={onClose}
      onDone={onDone}
    >
      <Form.Item
        name="invoice_no"
        label={t("finance.invoiceNoLabel")}
        rules={[{ required: true, min: 2, message: t("finance.invoiceNoRule") }]}
      >
        <Input placeholder={t("finance.invoiceNoPlaceholder")} maxLength={64} />
      </Form.Item>
    </RowActionModal>
  );
}

export function InvoicesTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const { formatMoney } = useFormat();
  const role = useAdminRole();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  // 筛选条件入 URL(status/账期)
  const { search, setFilters } = useFinanceFilters();
  const status = search.i_status;
  const urlPeriod = search.i_period ?? "";
  // 账期 commit 制;URL 回流走渲染期派生态
  const [periodInput, setPeriodInput] = useState(urlPeriod);
  const [prevPeriod, setPrevPeriod] = useState(urlPeriod);
  if (urlPeriod !== prevPeriod) {
    setPrevPeriod(urlPeriod);
    setPeriodInput(urlPeriod);
  }
  // 非 YYYY-MM 标红提示,不阻止提交
  const periodBad = periodInput.trim() !== "" && !PERIOD_RE.test(periodInput.trim());
  // 抬头与邮箱默认脱敏;reveal=true + 必填事由回明文,授权绑定当时筛选口径,换筛选即删授权
  const filterKey = `${status ?? ""}|${urlPeriod}`;
  const [reveal, setReveal] = useState<{ reason: string; filterKey: string } | null>(null);
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (filterKey !== prevFilterKey) {
    setPrevFilterKey(filterKey);
    setReveal(null);
  }
  const revealReason = reveal?.filterKey === filterKey ? reveal.reason : null;
  const [revealOpen, setRevealOpen] = useState(false);
  const [reasonInput, setReasonInput] = useState("");
  const params = {
    ...(status ? { status } : {}),
    ...(urlPeriod ? { period: urlPeriod } : {}),
    ...(revealReason !== null ? { reveal: true, reason: revealReason } : {}),
  };
  const { data, queryKey, isLoading, isError, error, refetch } = useInvoices(params);
  const { doExport, exporting } = useCsvExport((tz, lang) => exportInvoicesCsv(params, tz, lang));
  const rows: InvoiceRow[] = data ?? [];
  const [issueTarget, setIssueTarget] = useState<InvoiceRow | null>(null);
  const reject = useRejectInvoice();
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const noPerm = t("finance.financeOnlyInvoice");

  return (
    <>
      <Space wrap style={{ marginBottom: 12 }} align="start">
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: 150 }}
          value={status}
          onChange={(v) => setFilters({ i_status: v })}
          options={Object.entries(invoiceStatusMap).map(([v, m]) => ({
            value: v,
            label: t(m.labelKey),
          }))}
        />
        <Form.Item
          style={{ marginBottom: 0 }}
          validateStatus={periodBad ? "error" : undefined}
          help={periodBad ? t("finance.periodFormatHint") : undefined}
        >
          <Input.Search
            allowClear
            placeholder={t("finance.filterPeriod")}
            style={{ width: 200 }}
            value={periodInput}
            onChange={(e) => setPeriodInput(e.target.value)}
            onSearch={(v) => setFilters({ i_period: v.trim() || undefined })}
          />
        </Form.Item>
        <Tooltip title={revealReason !== null ? t("finance.exportRevealNote") : ""}>
          <Button onClick={() => void doExport()} loading={exporting}>
            {t("common.exportCsv")}
          </Button>
        </Tooltip>
        {revealReason === null ? (
          <Button onClick={() => setRevealOpen(true)}>{t("finance.revealIdentity")}</Button>
        ) : (
          <Tag color="orange" closable onClose={() => setReveal(null)}>
            {t("finance.revealActive", { reason: revealReason })}
          </Tag>
        )}
      </Space>
      <Modal
        title={t("finance.revealTitle")}
        open={revealOpen}
        onCancel={() => setRevealOpen(false)}
        okText={t("finance.revealConfirm")}
        okButtonProps={{ disabled: !isValidReason(reasonInput) }}
        onOk={() => {
          setReveal({ reason: reasonInput.trim(), filterKey });
          setRevealOpen(false);
          setReasonInput("");
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Text type="secondary">{t("finance.revealHint")}</Typography.Text>
          <Input.TextArea
            rows={2}
            value={reasonInput}
            onChange={(e) => setReasonInput(e.target.value)}
            placeholder={t("finance.revealReasonPlaceholder")}
            maxLength={REASON_MAX_LEN}
            showCount
          />
        </Space>
      </Modal>
      <Table<InvoiceRow>
        scroll={{ x: 1100 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="id"
        loading={isLoading}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            />
          ),
        }}
        dataSource={rows}
        columns={[
          { title: t("finance.colPeriod"), dataIndex: "period", width: 90, fixed: "left" },
          tenantColumn(t("finance.colTenant")),
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            width: 100,
            render: (v: string) => formatMoney(v),
          },
          {
            title: t("finance.colTitleInfo"),
            ellipsis: true,
            render: (_, r) => (
              <>
                {r.title}
                <div style={{ color: adminColors.textMuted, fontSize: fontSize.caption }}>
                  {r.title_type === "company"
                    ? t("finance.invoiceTitleTypeCompany")
                    : t("finance.invoiceTitleTypePersonal")}
                  {r.tax_id ? ` · ${r.tax_id}` : ""}
                </div>
              </>
            ),
          },
          { title: t("finance.colEmail"), dataIndex: "email", width: 180, ellipsis: true },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            width: 90,
            render: (v: string) => {
              return <StatusTag map={invoiceStatusMap} value={v} />;
            },
          },
          {
            title: t("finance.colInvoiceInfo"),
            width: 190,
            render: (_, r) => {
              if (r.status === "issued") {
                return (
                  <span>
                    {r.invoice_no}
                    <div style={{ color: adminColors.textMuted, fontSize: fontSize.caption }}>
                      #{r.issued_by} · {r.issued_at ? formatDateTime(r.issued_at) : ""}
                    </div>
                  </span>
                );
              }
              if (r.status === "rejected") {
                return <span style={{ color: adminColors.textMuted }}>{r.reject_reason}</span>;
              }
              return "-";
            },
          },
          {
            title: t("finance.colAction"),
            width: 150,
            fixed: "right",
            render: (_, r) => {
              if (r.status !== "submitted") return null;
              return (
                <Space wrap>
                  <Tooltip title={writable ? "" : noPerm}>
                    <Button size="small" type="primary" disabled={!writable} onClick={() => setIssueTarget(r)}>
                      {t("finance.invoiceIssue")}
                    </Button>
                  </Tooltip>
                  <ReasonAction
                    label={t("finance.invoiceReject")}
                    target={`#${r.id} · ${formatMoney(r.amount)}`}
                    title={t("finance.invoiceRejectTitle")}
                    confirmText={t("finance.invoiceRejectConfirm", { id: r.id, amount: formatMoney(r.amount) })}
                    danger
                    disabled={!writable}
                    disabledReason={noPerm}
                    onSubmit={async (reason) => {
                      await reject.mutateAsync({ invoiceId: r.id, data: { reason } });
                      refresh();
                    }}
                  />
                </Space>
              );
            },
          },
          { title: t("finance.colCreatedAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
        ]}
      />
      <ListCapNote rows={rows.length} cap={LIST_CAPS.invoices} />
      <IssueInvoiceModal target={issueTarget} onClose={() => setIssueTarget(null)} onDone={refresh} />
    </>
  );
}
