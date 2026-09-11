/** 工单:status/category 筛选 + user_id/ticket_no 检索(游标分页)+ 详情抽屉。读:全角色;写:ops/admin。 */

import {
  formatDateTime,
  isTicketRepliable,
  metaOf,
  ticketCategoryMap,
  ticketStatusMap,
} from "@superdl/ui";
import { HexTag, LoadMore, PageContainer, TableErrorEmpty, TicketBubble } from "@superdl/ui/components";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Drawer,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AdminTicketOut } from "@superdl/api-client";
import {
  isApiError,
  useReplyTicket,
  useTicketDetail,
  useTicketPendingCount,
  useTickets,
  useUpdateTicketStatus,
} from "../../api";
import { TenantLink } from "../../components/TenantLink";
import { useApiErrorText } from "@superdl/ui";
import { canWriteOps, useAdminRole } from "../../stores/auth";

const TICKET_STATUSES = Object.keys(ticketStatusMap);
const TICKET_CATEGORIES = Object.keys(ticketCategoryMap);

export const Route = createFileRoute("/_app/tickets")({
  // 筛选入 URL;id = 告警深链(自动开详情抽屉)
  validateSearch: (search: Record<string, unknown>): {
    status?: string;
    category?: string;
    id?: number;
    user_id?: number;
    ticket_no?: string;
  } => ({
    status:
      typeof search.status === "string" && TICKET_STATUSES.includes(search.status)
        ? search.status
        : undefined,
    category:
      typeof search.category === "string" && TICKET_CATEGORIES.includes(search.category)
        ? search.category
        : undefined,
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
    ticket_no:
      typeof search.ticket_no === "string" && search.ticket_no ? search.ticket_no : undefined,
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
  const qc = useQueryClient();
  const detail = useTicketDetail(ticketId);
  const [draft, setDraft] = useState("");
  const reply = useReplyTicket();
  const updateStatus = useUpdateTicketStatus();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "tickets"] });
    void qc.invalidateQueries({ queryKey: ["admin", "ticket", ticketId] });
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
        onError: (e) => message.error(errText(e, t("tickets.replyFailed"))),
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
        onError: (e) => message.error(errText(e, t("tickets.statusFailed"))),
      },
    );
  };

  return (
    <Drawer
      open={ticketId !== null}
      onClose={onClose}
      width="min(640px, 100vw)"
      title={
        ticket ? (
          <Space size={8} wrap>
            <Typography.Text code>{ticket.ticket_no}</Typography.Text>
            {cm && <Tag>{t(cm.labelKey)}</Tag>}
            {sm && <HexTag color={sm.color}>{t(sm.labelKey)}</HexTag>}
          </Space>
        ) : (
          t("tickets.detailTitle")
        )
      }
    >
      {/* 详情查询三态:骨架 / 失败可重试 */}
      {detail.isPending && ticketId !== null && <Skeleton active paragraph={{ rows: 6 }} />}
      {detail.isError && (
        <TableErrorEmpty
          isError
          onRetry={() => void detail.refetch()}
          compact
        >
          {null}
        </TableErrorEmpty>
      )}
      {ticket && (
        <Space orientation="vertical" size={16} style={{ width: "100%" }}>
          <div>
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              {ticket.subject}
            </Typography.Title>
            <Space size={16} wrap>
              <TenantLink id={ticket.user_id} />
              <Typography.Text type="secondary">
                {t("tickets.colCreatedAt")}: {formatDateTime(ticket.created_at)}
              </Typography.Text>
              {ticket.instance_uuid && (
                <Typography.Text type="secondary">
                  {t("tickets.instanceUuid")}: {ticket.instance_uuid}
                </Typography.Text>
              )}
            </Space>
          </div>
          <Space orientation="vertical" size={12} style={{ width: "100%" }}>
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
              <Tooltip title={writable ? "" : noPerm}>
                <Button
                  type="primary"
                  style={{ height: "auto" }}
                  loading={reply.isPending}
                  disabled={!writable || draft.trim().length < 2}
                  onClick={sendReply}
                >
                  {t("tickets.replySend")}
                </Button>
              </Tooltip>
            </Space.Compact>
          ) : (
            <Alert type="info" showIcon title={t("tickets.terminalHint")} />
          )}
          <Space wrap>
            {ticket.status !== "resolved" && ticket.status !== "closed" && (
              <Popconfirm title={t("tickets.resolveConfirm")} onConfirm={() => setStatus("resolve")}>
                <Tooltip title={writable ? "" : noPerm}>
                  <Button type="primary" disabled={!writable} loading={updateStatus.isPending}>
                    {t("tickets.resolve")}
                  </Button>
                </Tooltip>
              </Popconfirm>
            )}
            {ticket.status === "resolved" && (
              <Popconfirm title={t("tickets.closeConfirm")} onConfirm={() => setStatus("close")}>
                <Tooltip title={writable ? "" : noPerm}>
                  <Button disabled={!writable} loading={updateStatus.isPending}>
                    {t("tickets.close")}
                  </Button>
                </Tooltip>
              </Popconfirm>
            )}
          </Space>
        </Space>
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
  // 文本检索 commit 制:回车/失焦/点搜索才回写 URL
  const userId = search.user_id ?? null;
  const ticketNo = search.ticket_no ?? "";
  const [userIdInput, setUserIdInput] = useState<number | null>(userId);
  const [ticketNoInput, setTicketNoInput] = useState(ticketNo);
  // URL 变化回流进输入框(渲染期派生态)
  const filterKey = `${search.user_id ?? ""}|${search.ticket_no ?? ""}`;
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (filterKey !== prevFilterKey) {
    setPrevFilterKey(filterKey);
    setUserIdInput(userId);
    setTicketNoInput(ticketNo);
  }
  const ticketsQ = useTickets({
    ...(status ? { status } : {}),
    ...(category ? { category } : {}),
    ...(userId != null ? { user_id: userId } : {}),
    ...(ticketNo.trim() ? { ticket_no: ticketNo.trim() } : {}),
  });
  const { data, queryKey, isLoading, isError, error, refetch, hasNextPage, fetchNextPage, isFetchingNextPage, isFetchNextPageError } = ticketsQ;
  // 待客服计数角标(60s 轮询);点击按该口径过滤
  const pendingQ = useTicketPendingCount();
  const rows: AdminTicketOut[] = (data?.pages ?? []).flatMap((p) => p.items);
  const [openId, setOpenId] = useState<number | null>(null);
  // 告警深链(/tickets?id=<id>)自动开详情抽屉
  const [prevSearchId, setPrevSearchId] = useState(search.id);
  if (search.id !== prevSearchId) {
    setPrevSearchId(search.id);
    if (search.id != null) setOpenId(search.id);
  }
  const setFilters = (next: { status?: string; category?: string; user_id?: number; ticket_no?: string }) =>
    void navigate({
      to: "/tickets",
      replace: true,
      search: (prev) => ({ ...prev, ...next }),
    });

  return (
    <PageContainer
      title={t("tickets.title")}
      extra={
        <Space wrap>
          <InputNumber
            placeholder={t("tickets.filterUserId")}
            style={{ width: 130 }}
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
            style={{ width: 180 }}
            value={ticketNoInput}
            onChange={(e) => setTicketNoInput(e.target.value)}
            onSearch={(v) => setFilters({ ticket_no: v || undefined })}
          />
          <Select
            allowClear
            placeholder={t("tickets.filterStatus")}
            style={{ width: 160 }}
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
            style={{ width: 160 }}
            value={category}
            onChange={(v) => setFilters({ category: v })}
            options={Object.entries(ticketCategoryMap).map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
          />
          <Button onClick={() => void qc.resetQueries({ queryKey })}>{t("common.refresh")}</Button>
          {pendingQ.data != null && pendingQ.data.count > 0 && (
            <Button type="primary" ghost onClick={() => setFilters({ status: "pending_staff" })}>
              <Badge count={pendingQ.data.count} size="small" offset={[6, -2]}>
                {t("tickets.pendingStaff")}
              </Badge>
            </Button>
          )}
        </Space>
      }
    >
      <Card>
      <Table<AdminTicketOut>
        scroll={{ x: 960 }}
        rowKey="id"
        loading={isLoading}
        dataSource={rows}
        pagination={false}
        locale={{
          emptyText: (
            <TableErrorEmpty
              isError={isError}
              isForbidden={isApiError(error) && error.status === 403}
              onRetry={() => void refetch()}
            >
              {t("tickets.empty")}
            </TableErrorEmpty>
          ),
        }}
        onRow={(r) => ({ onClick: () => setOpenId(r.id), style: { cursor: "pointer" } })}
        columns={[
          { title: t("tickets.colTicketNo"), dataIndex: "ticket_no", width: 140 },
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
              const m = metaOf(ticketStatusMap, v);
              return (
                <Badge
                  status={m?.badge ?? "default"}
                  text={m ? t(m.labelKey) : v}
                />
              );
            },
          },
          { title: t("tickets.colUpdatedAt"), dataIndex: "updated_at", width: 150, render: formatDateTime },
          { title: t("tickets.colCreatedAt"), dataIndex: "created_at", width: 150, render: formatDateTime },
          {
            // 文字按钮保证键盘可达
            title: t("tickets.colActions"),
            width: 80,
            render: (_, r) => (
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
            ),
          },
        ]}
      />
      <LoadMore
        hasNextPage={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        isError={isFetchNextPageError}
        loadedCount={rows.length}
        onLoadMore={() => void fetchNextPage()}
      />
      </Card>
      <TicketDrawer ticketId={openId} onClose={() => setOpenId(null)} writable={writable} />
    </PageContainer>
  );
}
