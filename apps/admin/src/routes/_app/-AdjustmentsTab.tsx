/** 调账 Tab:FilterBar(状态 / 租户 id / 发起日,入 URL)+ 发起(草稿)+ 双人复核(批准需核对勾选,驳回需理由;复核列固定右)。 */

import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Checkbox,
  DatePicker,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  addAmounts,
  adjustmentStatusMap,
  controlWidth,
  flattenPages,
  adminColors,
  fontSize,
  formatDateTime,
  idemKeyOf,
  layout,
  ledgerTypeMap,
  metaOf,
} from "@superdl/ui";
import { CursorTable, FilterBar, GatedButton, moneyOr, RowActions } from "@superdl/ui/components";
import { useApiErrorText, useUrlFilters } from "@superdl/ui";
import { useCsvExport, useFormDraft } from "@superdl/ui";
import { useFormat } from "@superdl/ui";

import {
  type AdjustmentRow,
  exportAdjustmentsCsv,
  useAdjustContext,
  useAdjustments,
  useCreateAdjustment,
  useReviewAdjustment,
} from "../../api";
import { isValidReason, REASON_MAX_LEN } from "../../lib/validators";
import { SignedAmount } from "../../components/SignedAmount";
import { StatusTag } from "@superdl/ui/components";
import { tenantColumn } from "../../components/TenantLink";
import { canWriteFinance, useAdminRole, useAuth } from "../../stores/auth";
import { useFinanceFilters } from "./-financeFilters";

// 单笔绝对值上限,与 adminapi/service.ADJUST_MAX_ABS 对齐
export const ADJUST_MAX_ABS = 100000;

/** 复核确认框:租户/当前余额/调账后余额/发起人/原因;驳回必填理由(入审计)。 */
export function ReviewConfirmModal({
  target,
  onClose,
  onReviewed,
}: {
  target: { adj: AdjustmentRow; approve: boolean } | null;
  onClose: () => void;
  onReviewed: () => void;
}) {
  const { t } = useTranslation();
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const ctx = useAdjustContext(target?.adj.user_id ?? null);
  const [rejectReason, setRejectReason] = useState("");
  // 批准是入账动作:核对勾选后才放行(与驳回的必填理由对称)
  const [acked, setAcked] = useState(false);
  const review = useReviewAdjustment({
    mutation: {
      onSuccess: () => {
        message.success(t("finance.reviewed"));
        setRejectReason("");
        onReviewed();
        onClose();
      },
      onError: (e) => message.error(errText(e, t("finance.reviewFailed"))),
    },
  });
  if (!target) return null;
  const { adj, approve } = target;
  // 事后余额:BigInt 相加(禁浮点)
  const after = ctx.data ? addAmounts(ctx.data.balance, adj.amount) : null;
  return (
    <Modal
      open
      title={approve ? t("finance.approveTitle") : t("finance.rejectTitle")}
      okText={approve ? t("finance.approve") : t("finance.reject")}
      okButtonProps={{
        danger: !approve,
        loading: review.isPending,
        disabled: ctx.isError || (approve ? !acked : !isValidReason(rejectReason)),
      }}
      onCancel={onClose}
      onOk={() =>
        review.mutate({
          adjustmentId: adj.id,
          data: approve ? { approve: true } : { approve: false, comment: rejectReason.trim() },
        })
      }
    >
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label={t("finance.colTenant")}>
          #{adj.user_id}
          {ctx.data ? ` · ${ctx.data.phone_masked}` : ""}
          {ctx.data?.status === "frozen" ? ` · ${t("tenants.frozen")}` : ""}
        </Descriptions.Item>
        <Descriptions.Item label={t("finance.colAmount")}>
          <SignedAmount value={adj.amount} />
        </Descriptions.Item>
        <Descriptions.Item label={t("finance.ctxBalance")}>
          {ctx.isLoading ? "…" : moneyOr(formatMoney(ctx.data?.balance ?? "0.00"), ctx.data != null)}
        </Descriptions.Item>
        {approve && (
          <Descriptions.Item label={t("finance.ctxBalanceAfter")}>
            {moneyOr(formatMoney(after ?? "0.00"), after !== null)}
          </Descriptions.Item>
        )}
        <Descriptions.Item label={t("finance.colCreatedBy")}>#{adj.created_by}</Descriptions.Item>
        <Descriptions.Item label={t("finance.colReason")}>{adj.reason}</Descriptions.Item>
      </Descriptions>
      {approve && (
        <Checkbox checked={acked} onChange={(e) => setAcked(e.target.checked)} style={{ marginTop: 12 }}>
          {t("finance.approveAck")}
        </Checkbox>
      )}
      {!approve && (
        <Form layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            label={t("finance.rejectReasonLabel")}
            required
            validateStatus={rejectReason !== "" && !isValidReason(rejectReason) ? "error" : undefined}
            help={rejectReason !== "" && !isValidReason(rejectReason) ? t("common.reasonRule") : undefined}
          >
            <Input.TextArea
              rows={2}
              maxLength={REASON_MAX_LEN}
              showCount
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder={t("finance.rejectReasonPlaceholder")}
            />
          </Form.Item>
        </Form>
      )}
      {ctx.isError && <Alert type="error" showIcon style={{ marginTop: 12 }} title={t("finance.tenantNotFound")} />}
    </Modal>
  );
}

export function AdjustmentsTab() {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { formatMoney } = useFormat();
  const { message } = App.useApp();
  const role = useAdminRole();
  const { admin } = useAuth();
  const writable = canWriteFinance(role);
  const qc = useQueryClient();
  // 筛选条件入 URL(status/发起日/租户 id)
  const { search, setFilters } = useFinanceFilters();
  const status = search.a_status;
  const day = search.a_day ? dayjs(search.a_day) : null;
  const filters = useUrlFilters({
    search: { a_status: status, a_day: search.a_day, a_uid: search.a_uid },
    keys: ["a_status", "a_day", "a_uid"],
    commit: setFilters,
  });
  // 租户 id commit 制;URL 回流走渲染期派生态
  const [uidInput, setUidInput] = useState<number | null>(search.a_uid ?? null);
  const [prevUid, setPrevUid] = useState(search.a_uid);
  if (search.a_uid !== prevUid) {
    setPrevUid(search.a_uid);
    setUidInput(search.a_uid ?? null);
  }
  const commitUid = () => setFilters({ a_uid: uidInput ?? undefined });
  const [creating, setCreating] = useState(false);
  const [reviewTarget, setReviewTarget] = useState<{ adj: AdjustmentRow; approve: boolean } | null>(null);
  const [form] = Form.useForm<{ user_id: number; amount: string; reason: string }>();
  // 新建草稿(sessionStorage),发起成功后清除
  const draft = useFormDraft<{ user_id: number; amount: string; reason: string }>("adjustment-new");
  const refresh = () => void qc.invalidateQueries({ queryKey });
  const params = {
    ...(status ? { status } : {}),
    ...(search.a_day ? { day: search.a_day } : {}),
    ...(search.a_uid ? { user_id: search.a_uid } : {}),
  };
  const adjustmentsQ = useAdjustments(params);
  const { data, queryKey } = adjustmentsQ;
  const { doExport, exporting } = useCsvExport((tz, lang) => exportAdjustmentsCsv(params, tz, lang));
  const rows = flattenPages(data);
  const total = data?.pages[0]?.total ?? undefined;

  // 输入 user_id 回显租户身份与资金现状;不存在阻止提交
  const wUserId = Form.useWatch("user_id", form);
  const ctxId = typeof wUserId === "number" && Number.isInteger(wUserId) && wUserId > 0 ? wUserId : null;
  const ctx = useAdjustContext(creating ? ctxId : null);

  const create = useCreateAdjustment({
    mutation: {
      onSuccess: () => {
        message.success(t("finance.adjustCreated"));
        setCreating(false);
        form.resetFields();
        draft.clear();
        refresh();
      },
      onError: (e) => message.error(errText(e, t("finance.createFailed"))),
    },
  });

  return (
    <>
      <FilterBar
        hasFilter={filters.hasFilter}
        onClear={filters.clear}
        count={total}
        extra={
          <>
            <Button onClick={() => void doExport()} loading={exporting}>
              {t("common.exportCsv")}
            </Button>
            <GatedButton
              type="primary"
              reason={writable ? undefined : t("finance.financeOnlyCreate")}
              onClick={() => {
                setCreating(true);
                const d = draft.load();
                if (d) form.setFieldsValue(d);
              }}
            >
              {t("finance.createAdjust")}
            </GatedButton>
          </>
        }
      >
        <Select
          allowClear
          placeholder={t("common.statusFilter")}
          style={{ width: controlWidth.sm }}
          value={status}
          onChange={(v) => setFilters({ a_status: v })}
          options={Object.entries(adjustmentStatusMap).map(([v, m]) => ({ value: v, label: t(m.labelKey) }))}
        />
        <InputNumber
          min={1}
          precision={0}
          controls={false}
          placeholder={t("finance.filterTenantId")}
          style={{ width: controlWidth.sm }}
          value={uidInput}
          onChange={(v) => setUidInput(v ?? null)}
          onBlur={commitUid}
          onPressEnter={commitUid}
        />
        <DatePicker
          value={day}
          onChange={(d) => setFilters({ a_day: d ? d.format("YYYY-MM-DD") : undefined })}
          allowClear
        />
      </FilterBar>
      <CursorTable<AdjustmentRow>
        query={adjustmentsQ}
        rows={rows}
        empty={filters.hasFilter ? t("empty.search", { ns: "shared" }) : undefined}
        scroll={{ x: 1000 }}
        sticky={{ offsetHeader: layout.topBarHeight }}
        rowKey="id"
        columns={[
          { title: t("finance.colAdjustId"), dataIndex: "id", width: 70, fixed: "left" },
          tenantColumn(t("finance.colTenant")),
          {
            title: t("finance.colAmount"),
            dataIndex: "amount",
            align: "right",
            render: (v: string) => <SignedAmount value={v} />,
          },
          { title: t("finance.colReason"), dataIndex: "reason", ellipsis: true },
          {
            title: t("finance.colStatus"),
            dataIndex: "status",
            render: (v: string) => {
              return <StatusTag map={adjustmentStatusMap} value={v} />;
            },
          },
          { title: t("finance.colCreatedBy"), dataIndex: "created_by", width: 80 },
          { title: t("finance.colCreatedAtShort"), dataIndex: "created_at", render: formatDateTime },
          {
            title: t("finance.colReview"),
            fixed: "right",
            width: 160,
            render: (_, r) => {
              if (r.status !== "pending") {
                return (
                  <span style={{ color: adminColors.textMuted }}>
                    {r.reviewed_by ? `#${r.reviewed_by} ${r.review_comment ?? ""}` : "-"}
                  </span>
                );
              }
              const isCreator = admin?.id === r.created_by;
              const reason = !writable
                ? t("finance.financeOnlyReview")
                : isCreator
                  ? t("finance.noSelfReviewShort")
                  : undefined;
              // 双向决定:批准 / 驳回并列可见
              return (
                <RowActions
                  primary={
                    <GatedButton
                      size="small"
                      type="primary"
                      reason={reason}
                      onClick={() => setReviewTarget({ adj: r, approve: true })}
                    >
                      {t("finance.approve")}
                    </GatedButton>
                  }
                  secondary={
                    <GatedButton
                      size="small"
                      danger
                      reason={reason}
                      onClick={() => setReviewTarget({ adj: r, approve: false })}
                    >
                      {t("finance.reject")}
                    </GatedButton>
                  }
                />
              );
            },
          },
        ]}
      />
      <ReviewConfirmModal target={reviewTarget} onClose={() => setReviewTarget(null)} onReviewed={refresh} />
      <Modal
        title={t("finance.createAdjustTitle")}
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => {
          void (async () => {
            let values: { user_id: number; amount: string; reason: string };
            try {
              values = await form.validateFields();
            } catch {
              return; // 校验失败:antd 已就地标红
            }
            // 上下文未确认(不存在/查询失败)阻止提交
            if (!ctx.data) return;
            create.mutate({
              data: {
                user_id: values.user_id,
                amount: values.amount,
                reason: values.reason,
              },
              // 幂等键从表单快照派生,失败不轮换
              idempotencyKey: idemKeyOf("adj", [values.user_id, values.amount, values.reason]),
            });
          })();
        }}
        okButtonProps={{ loading: create.isPending, disabled: ctxId === null || !ctx.data }}
      >
        <Form
          form={form}
          layout="vertical"
          onValuesChange={() =>
            draft.save(form.getFieldsValue(true) as Partial<{ user_id: number; amount: string; reason: string }>)
          }
        >
          <Form.Item name="user_id" label={t("finance.tenantIdLabel")} rules={[{ required: true }]}>
            <InputNumber min={1} precision={0} style={{ width: "100%" }} />
          </Form.Item>
          {ctxId !== null && (
            <div style={{ marginTop: -8, marginBottom: 16 }}>
              {ctx.isLoading && <Spin size="small" />}
              {ctx.isError && <Typography.Text type="danger">{t("finance.tenantNotFound")}</Typography.Text>}
              {ctx.data && (
                <Alert
                  type={ctx.data.status === "frozen" ? "warning" : "info"}
                  showIcon
                  title={
                    <Space size={12} wrap>
                      <span>{ctx.data.phone_masked}</span>
                      <span>{ctx.data.status === "frozen" ? t("tenants.frozen") : t("tenants.active")}</span>
                      <span>
                        {t("finance.ctxBalance")}:<b>{formatMoney(ctx.data.balance)}</b>
                      </span>
                      <span>{t("finance.ctxRunning", { count: ctx.data.running_instances })}</span>
                    </Space>
                  }
                  description={
                    ctx.data.recent_ledger.length > 0 ? (
                      <Space orientation="vertical" size={2} style={{ width: "100%" }}>
                        {ctx.data.recent_ledger.map((l) => (
                          <span key={l.id} style={{ fontSize: fontSize.caption }}>
                            {formatDateTime(l.created_at)} ·{" "}
                            {(() => {
                              const m = metaOf(ledgerTypeMap, l.type);
                              return m ? t(m.labelKey) : l.type;
                            })()}{" "}
                            · {formatMoney(l.amount)}
                            {l.remark ? ` · ${l.remark}` : ""}
                          </span>
                        ))}
                      </Space>
                    ) : undefined
                  }
                />
              )}
            </div>
          )}
          <Form.Item name="amount" label={t("finance.amountLabel")} rules={[{ required: true }]}>
            {/* stringMode:金额以字符串提交;上限与后端 ADJUST_MAX_ABS 对齐 */}
            <InputNumber
              step="0.01"
              precision={2}
              stringMode
              min={String(-ADJUST_MAX_ABS)}
              max={String(ADJUST_MAX_ABS)}
              style={{ width: "100%" }}
              placeholder={t("finance.amountPlaceholder")}
            />
          </Form.Item>
          <Form.Item name="reason" label={t("common.reasonLabel")} rules={[{ required: true, min: 2 }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
