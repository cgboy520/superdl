/** 工单:FilterBar(待回复 Segmented / 状态 / 分类 / user_id / 工单号,入 URL;游标分页)+ 详情抽屉。读:全角色;写:ops/admin。 */

import {
  controlWidth,
  drawerWidth,
  flattenPages,
  formatDateTime,
  isTicketRepliable,
  metaOf,
  space,
  ticketCategoryMap,
  ticketStatusMap,
  useUrlFilters,
} from "@superdl/ui";
import {
  CursorTable,
  EmptyState,
  FilterBar,
  GatedButton,
  HexTag,
  Mono,
  PageContainer,
  RowActions,
  TableErrorEmpty,
  TicketBubble,
  useConfirm,
} from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Input,
  InputNumber,
  Segmented,
  Select,
  Skeleton,
  Space,
  Tag,
  theme,
  Typography,
} from "antd";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminTicketOut } from "@superdl/api-client";
import {
  adminKeys,
  useReplyTicket,
  useTicketDetail,
  useTicketPendingCount,
  useTickets,
  useUpdateTicketStatus,
} from "../../api";
import { StatusTag } from "@superdl/ui/components";
import { TenantLink } from "../../components/TenantLink";
import { useApiErrorText, useUrlCommittedInput } from "@superdl/ui";
import { canWriteOps, useAdminRole } from "../../stores/auth";

const TICKET_STATUSES = Object.keys(ticketStatusMap);
const TICKET_CATEGORIES = Object.keys(ticketCategoryMap);

export const Route = createFileRoute("/_app/tickets")({
  validateSearch: (
    search: Record<string, unknown>,
  ): {
    status?: string;
    category?: string;
    id?: number;
    user_id?: number;
    ticket_no?: string;
  } => ({
    status: typeof search.status === "string" && TICKET_STATUSES.includes(search.status) ? search.status : undefined,
    category:
      typeof search.category === "string" && TICKET_CATEGORIES.includes(search.category) ? search.category : undefined,
    id:
      typeof search.id === "number" && Number.isInteger(search.id) && search.id > 0
        ? search.id
        : typeof search.id === "string" && /^\d+$/.test(search.id)
          ? Number(search.id)
          : undefined,
    user_id:
      typeof search.user_id === "number" && Number.isInteger(search.user_id) && search.user_id > 0
        ? search.user_id
        : typeof search.user_id === "string" && /^\d+$/.test(search.user_id)
          ? Number(search.user_id)
          : undefined,
    ticket_no: typeof search.ticket_no === "string" && search.ticket_no ? search.ticket_no : undefined,
  }),
  component: TicketsPage,
});

function TicketDrawer({
  ticketId,
  onClose,
  writable,
}: {
  ticketId: number | null;
  onClose: () => void;
  writable: boolean;
}) {
  const { t } = useTranslation(["admin", "shared"]);
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const qc = useQueryClient();
  const detail = useTicketDetail(ticketId);
  const [draft, setDraft] = useState("");
  const reply = useReplyTicket();
  const updateStatus = useUpdateTicketStatus();
  const confirm = useConfirm();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: adminKeys.tickets.all });
    void qc.invalidateQueries({ queryKey: adminKeys.tickets.detail(ticketId) });
  };

  const ticket = detail.data;
  const sm = ticket ? metaOf(ticketStatusMap, ticket.status) : undefined;
  const cm = ticket ? metaOf(ticketCategoryMap, ticket.category) : undefined;
  const repliable = ticket != null && isTicketRepliable(ticket.status);
  const noPerm = t("tickets.opsOnly");

  const sendReply = () => {
    if (ticketId === null || draft.trim().length < 2) return;
    reply.mutate(
      { ticketId, data: { body: draft.trim() } },
      {
        onSuccess: () => {
          setDraft("");
          message.success(t("tickets.replied"));
          refresh();
        },
        onError: (e) => {
          message.error(errText(e, t("tickets.replyFailed")));
        },
      },
    );
  };
  const setStatus = (action: "resolve" | "close") => {
    if (ticketId === null) return;
    updateStatus.mutate(
      { ticketId, data: { action } },
      {
        onSuccess: () => {
          message.success(t(action === "resolve" ? "tickets.resolvedOk" : "tickets.closedOk"));
          refresh();
        },
        onError: (e) => {
          message.error(errText(e, t("tickets.statusFailed")));
        },
      },
    );
  };

  return (
    <Drawer
      open={ticketId !== null}
      onClose={onClose}
      size={drawerWidth.md}
      title={
        ticket ? (
          <Space size={space.sm} wrap>
            <Typography.Text code>{ticket.ticket_no}</Typography.Text>
            {cm && <Tag>{t(cm.labelKey)}</Tag>}
            {sm && <HexTag color={sm.color}>{t(sm.labelKey)}</HexTag>}
          </Space>
        ) : (
          t("tickets.detailTitle")
        )
      }
      footer={
        ticket ? (
          <Space wrap style={{ display: "flex", justifyContent: "flex-end" }}>
            {ticket.status !== "resolved" && ticket.status !== "closed" && (
              <GatedButton
                type="primary"
                reason={writable ? undefined : noPerm}
                loading={updateStatus.isPending}
                onClick={() =>
                  confirm({
                    title: t("tickets.resolveConfirm", { no: ticket.ticket_no }),
                    consequences: [t("tickets.resolveBody")],
                    okText: t("tickets.resolve"),
                    onOk: () => setStatus("resolve"),
                  })
                }
              >
                {t("tickets.resolve")}
              </GatedButton>
            )}
            {ticket.status === "resolved" && (
              <GatedButton
                reason={writable ? undefined : noPerm}
                loading={updateStatus.isPending}
                onClick={() =>
                  confirm({
                    title: t("tickets.closeConfirm", { no: ticket.ticket_no }),
                    consequences: [t("tickets.closeBody")],
                    okText: t("tickets.close"),
                    onOk: () => setStatus("close"),
                  })
                }
              >
                {t("tickets.close")}
              </GatedButton>
            )}
          </Space>
        ) : null
      }
    >
      {detail.isPending && ticketId !== null && <Skeleton active paragraph={{ rows: 6 }} />}
      {detail.isError && (
        <TableErrorEmpty isError onRetry={() => void detail.refetch()} compact>
          {null}
        </TableErrorEmpty>
      )}
      {ticket && (
        <div style={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
          <Space orientation="vertical" size={space.lg} style={{ width: "100%", flex: 1 }}>
            <div>
              <Typography.Title level={5} style={{ marginTop: 0 }}>
                {ticket.subject}
              </Typography.Title>
              <Space size={space.lg} wrap>
                <TenantLink id={ticket.user_id} />
                <Typography.Text type="secondary">
                  {t("tickets.colCreatedAt")}: {formatDateTime(ticket.created_at)}
                </Typography.Text>
                {ticket.instance_uuid && (
                  <Typography.Text type="secondary">
                    {t("tickets.instanceUuid")}: <Mono>{ticket.instance_uuid}</Mono>
                  </Typography.Text>
                )}
              </Space>
            </div>
            <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
              {(ticket.messages ?? []).map((m) => (
                <TicketBubble
                  key={m.id}
                  side={m.sender_kind === "staff" ? "right" : "left"}
                  label={m.sender_kind === "staff" ? t("tickets.msgStaff") : t("tickets.msgUser")}
                  time={formatDateTime(m.created_at)}
                  body={m.body}
                  maxWidth="85%"
                />
              ))}
            </Space>
          </Space>
          <div
            style={{
              position: "sticky",
              bottom: 0,
              background: token.colorBgElevated,
              paddingTop: space.md,
            }}
          >
            {repliable ? (
              <Space.Compact style={{ width: "100%" }}>
                <Input.TextArea
                  rows={3}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  maxLength={4000}
                  placeholder={t("tickets.replyPlaceholder")}
                  aria-label={t("tickets.replyLabel")}
                  disabled={!writable}
                />
                <GatedButton
                  type="primary"
                  style={{ height: "auto" }}
                  loading={reply.isPending}
                  reason={writable ? undefined : noPerm}
                  disabled={draft.trim().length < 2}
                  onClick={sendReply}
                >
                  {t("tickets.replySend")}
                </GatedButton>
              </Space.Compact>
            ) : (
              <Alert type="info" showIcon title={t("tickets.terminalHint")} />
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}

function TicketsPage() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate({ from: "/tickets" });
  const qc = useQueryClient();
  const role = useAdminRole();
  const writable = canWriteOps(role);
  const search = Route.useSearch();
  const status = search.status;
  const category = search.category;
  const userId = search.user_id ?? null;
  const ticketNo = search.ticket_no;
  const [userIdInput, setUserIdInput] = useState<number | null>(userId);
  const [prevUserId, setPrevUserId] = useState(userId);
  if (userId !== prevUserId) {
    setPrevUserId(userId);
    setUserIdInput(userId);
  }
  const ticketsQ = useTickets({
    ...(status ? { status } : {}),
    ...(category ? { category } : {}),
    ...(userId != null ? { user_id: userId } : {}),
    ...(ticketNo?.trim() ? { ticket_no: ticketNo.trim() } : {}),
  });
  const { data, queryKey } = ticketsQ;
  const pendingQ = useTicketPendingCount();
  const rows = flattenPages(data);
  const total = data?.pages[0]?.total ?? undefined;
  const [openId, setOpenId] = useState<number | null>(null);
  const [prevSearchId, setPrevSearchId] = useState(search.id);
  if (search.id !== prevSearchId) {
    setPrevSearchId(search.id);
    if (search.id != null) setOpenId(search.id);
  }
  const setFilters = useCallback(
    (next: { status?: string; category?: string; user_id?: number; ticket_no?: string }) =>
      void navigate({
        to: "/tickets",
        replace: true,
        search: (prev) => ({ ...prev, ...next }),
      }),
    [navigate],
  );
  const filters = useUrlFilters({
    search: { status, category, user_id: search.user_id, ticket_no: ticketNo },
    keys: ["status", "category", "user_id", "ticket_no"],
    commit: setFilters,
  });
  const commitTicketNo = useCallback((next: string | undefined) => setFilters({ ticket_no: next }), [setFilters]);
  const { value: ticketNoInput, setValue: setTicketNoInput } = useUrlCommittedInput(ticketNo, commitTicketNo);
  const pendingLabel =
    pendingQ.data != null ? t("tickets.pendingStaffCount", { count: pendingQ.data.count }) : t("tickets.pendingStaff");

  return (
    <PageContainer title={t("tickets.title")}>
      <Card>
        <FilterBar
          hasFilter={filters.hasFilter}
          onClear={filters.clear}
          count={total}
          extra={<Button onClick={() => void qc.resetQueries({ queryKey })}>{t("common.refresh")}</Button>}
        >
          <Segmented
            value={status === "pending_staff" ? "pending_staff" : "all"}
            onChange={(v) => setFilters({ status: v === "pending_staff" ? "pending_staff" : undefined })}
            options={[
              { value: "all", label: t("tickets.filterAll") },
              { value: "pending_staff", label: pendingLabel },
            ]}
          />
          <Select
            allowClear
            placeholder={t("tickets.filterStatus")}
            style={{ width: controlWidth.sm }}
            value={status}
            onChange={(v) => setFilters({ status: v })}
            options={Object.entries(ticketStatusMap).map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
          />
          <Select
            allowClear
            placeholder={t("tickets.filterCategory")}
            style={{ width: controlWidth.sm }}
            value={category}
            onChange={(v) => setFilters({ category: v })}
            options={Object.entries(ticketCategoryMap).map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
          />
          <InputNumber
            placeholder={t("tickets.filterUserId")}
            style={{ width: controlWidth.sm }}
            value={userIdInput}
            onChange={(v) => setUserIdInput(v)}
            onBlur={() => setFilters({ user_id: userIdInput ?? undefined })}
            onPressEnter={() => setFilters({ user_id: userIdInput ?? undefined })}
            min={1}
            precision={0}
            controls={false}
          />
          <Input.Search
            allowClear
            placeholder={t("tickets.filterTicketNo")}
            style={{ width: controlWidth.sm }}
            value={ticketNoInput}
            onChange={(e) => setTicketNoInput(e.target.value)}
            onSearch={(v) => commitTicketNo(v || undefined)}
          />
        </FilterBar>
        <CursorTable<AdminTicketOut>
          query={ticketsQ}
          rows={rows}
          emptyNode={
            <EmptyState
              scene={filters.hasFilter ? "search" : "ticket"}
              compact
              description={filters.hasFilter ? undefined : t("tickets.empty")}
              secondaryAction={
                filters.hasFilter ? (
                  <Button size="small" onClick={filters.clear}>
                    {t("filter.clear", { ns: "shared" })}
                  </Button>
                ) : undefined
              }
            />
          }
          scroll={{ x: 960 }}
          rowKey="id"
          onRow={(r) => ({ onClick: () => setOpenId(r.id), style: { cursor: "pointer" } })}
          columns={[
            {
              title: t("tickets.colTicketNo"),
              dataIndex: "ticket_no",
              width: 140,
              render: (v: string) => <Mono>{v}</Mono>,
            },
            {
              title: t("tickets.colTenant"),
              dataIndex: "user_id",
              width: 80,
              render: (v: number) => <TenantLink id={v} />,
            },
            {
              title: t("tickets.colCategory"),
              dataIndex: "category",
              width: 110,
              render: (v: string) => {
                const m = metaOf(ticketCategoryMap, v);
                return m ? t(m.labelKey) : v;
              },
            },
            { title: t("tickets.colSubject"), dataIndex: "subject", ellipsis: true },
            {
              title: t("tickets.colStatus"),
              dataIndex: "status",
              width: 110,
              render: (v: string) => {
                return <StatusTag map={ticketStatusMap} value={v} variant="badge" />;
              },
            },
            { title: t("tickets.colUpdatedAt"), dataIndex: "updated_at", width: 150, render: formatDateTime },
            { title: t("tickets.colCreatedAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
            {
              title: t("tickets.colActions"),
              width: 80,
              render: (_, r) => (
                <RowActions
                  primary={
                    <Button
                      type="link"
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpenId(r.id);
                      }}
                    >
                      {t("tickets.detail")}
                    </Button>
                  }
                />
              ),
            },
          ]}
        />
      </Card>
      <TicketDrawer ticketId={openId} onClose={() => setOpenId(null)} writable={writable} />
    </PageContainer>
  );
}
