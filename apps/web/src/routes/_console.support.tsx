/** 支持:自助排查(FAQ 锚点)+ 联系客服(平台配置 support 组)+ 我的工单。
 *  新建工单走 Modal,详情为独立对话页。 */

import { formatDateTime, idemKeyOf, metaOf, ticketCategoryMap, ticketStatusMap } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Row,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import type { TicketCreate, TicketOut } from "@superdl/api-client";
import { useCreateTicket } from "../api/mutations";
import { useInstances, useSiteConfig, useTicketPages } from "../api/queries";
import { CopyButton } from "../components/common";
import { LoadMoreButton } from "../components/LoadMore";
import { DataErrorAlert, TableErrorEmpty } from "../components/QueryState";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/support")({
  beforeLoad: requireAuth,
  component: SupportPage,
});

/** 自助排查:静态锚点直达 /help 现有 FAQ(条目键与 help.tsx 的 FAQ_KEYS 对齐)。 */
const SELF_HELP_KEYS = ["createFailed", "billingStart", "dataPersist", "arrears"] as const;

function SelfHelpCard() {
  const { t } = useTranslation();
  return (
    <Card title={t("support.selfHelpTitle")} style={{ height: "100%" }}>
      <List
        size="small"
        dataSource={[...SELF_HELP_KEYS]}
        renderItem={(k) => (
          <List.Item style={{ paddingInline: 0 }}>
            {/* 动态键:与 help.faq 同款,字面量断言收敛到已登记键 */}
            <Link to="/help">{t(`support.selfHelp.${k}` as "support.selfHelp.createFailed")}</Link>
          </List.Item>
        )}
      />
      <Link to="/help">{t("support.selfHelpMore")}</Link>
    </Card>
  );
}

function ContactCard() {
  const { t } = useTranslation();
  const siteQ = useSiteConfig();
  const { data: site } = siteQ;
  const email = site?.support_email;
  const wechat = site?.support_wechat;
  return (
    <Card title={t("support.contactTitle")} style={{ height: "100%" }}>
      {siteQ.isError ? (
        // 加载失败绝不伪装成「联系方式未配置」
        <DataErrorAlert onRetry={() => void siteQ.refetch()} />
      ) : email || wechat ? (
        <Space orientation="vertical" size={12}>
          {wechat && (
            <Space>
              <Typography.Text strong>{t("support.contactWechat")}</Typography.Text>
              <Typography.Text code>{wechat}</Typography.Text>
              <CopyButton text={wechat} />
            </Space>
          )}
          {email && (
            <Space>
              <Typography.Text strong>{t("support.contactEmail")}</Typography.Text>
              <a href={`mailto:${email}`}>{email}</a>
              <CopyButton text={email} />
            </Space>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("support.contactHint")}
          </Typography.Text>
        </Space>
      ) : (
        <Alert type="info" showIcon title={t("support.contactMissing")} />
      )}
    </Card>
  );
}

function CreateTicketModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation(["web", "shared"]);
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [form] = Form.useForm<TicketCreate>();
  const { data: instances } = useInstances();
  // 幂等键按「提交序号 + 表单快照」派生:同一键重放返回既有单(双击/重试安全),成功后序号 +1 即新单
  const [submitSeq, setSubmitSeq] = useState(0);
  const create = useCreateTicket({
    onSuccess: (ticket) => {
      message.success(t("support.created"));
      setSubmitSeq((s) => s + 1);
      form.resetFields();
      onClose();
      void navigate({ to: "/support/$ticketId", params: { ticketId: String(ticket.id) } });
    },
  });
  const category = Form.useWatch("category", form);

  return (
    <Modal
      title={t("support.createTitle")}
      open={open}
      onCancel={onClose}
      okText={t("support.createSubmit")}
      okButtonProps={{ loading: create.isPending }}
      onOk={async () => {
        const values = await form.validateFields();
        create.mutate({
          body: values,
          idempotencyKey: idemKeyOf("ticket", [
            submitSeq,
            values.category,
            values.instance_uuid ?? null,
            values.subject,
            values.body,
          ]),
        });
      }}
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t("support.createNote")}
      </Typography.Paragraph>
      <Form form={form} layout="vertical" initialValues={{ category: "instance" }}>
        <Form.Item name="category" label={t("support.colCategory")} rules={[{ required: true }]}>
          <Select
            options={Object.entries(ticketCategoryMap).map(([v, m]) => ({
              value: v,
              label: t(m.labelKey),
            }))}
          />
        </Form.Item>
        {category === "instance" && (
          <Form.Item name="instance_uuid" label={t("support.fieldInstance")}>
            <Select
              allowClear
              placeholder={t("support.fieldInstancePlaceholder")}
              options={(instances ?? []).map((i) => ({ value: i.uuid, label: i.name }))}
            />
          </Form.Item>
        )}
        <Form.Item
          name="subject"
          label={t("support.fieldSubject")}
          rules={[{ required: true, min: 2, max: 128 }]}
        >
          <Input maxLength={128} showCount placeholder={t("support.fieldSubjectPlaceholder")} />
        </Form.Item>
        <Form.Item
          name="body"
          label={t("support.fieldBody")}
          rules={[{ required: true, min: 2, max: 4000 }]}
        >
          <Input.TextArea
            rows={5}
            maxLength={4000}
            showCount
            placeholder={t("support.fieldBodyPlaceholder")}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function SupportPage() {
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const tickets = useTicketPages(20);
  const rows = useMemo<TicketOut[]>(
    () => (tickets.data?.pages ?? []).flatMap((p) => p.items),
    [tickets.data],
  );

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ marginBottom: 0 }}>
        {t("support.title")}
      </Typography.Title>
      <Row gutter={16}>
        <Col xs={24} md={12}>
          <SelfHelpCard />
        </Col>
        <Col xs={24} md={12}>
          <ContactCard />
        </Col>
      </Row>
      <Card
        title={t("support.myTickets")}
        extra={
          <Button type="primary" onClick={() => setCreating(true)}>
            {t("support.create")}
          </Button>
        }
      >
        <List
          loading={tickets.isLoading}
          dataSource={rows}
          locale={{
            emptyText: tickets.isError ? (
              // 失败绝不伪装成「暂无工单」
              <TableErrorEmpty onRetry={() => void tickets.refetch()} />
            ) : (
              <Empty description={t("support.none")} />
            ),
          }}
          renderItem={(r) => {
            const sm = metaOf(ticketStatusMap, r.status);
            const cm = metaOf(ticketCategoryMap, r.category);
            return (
              <List.Item
                style={{ cursor: "pointer" }}
                onClick={() =>
                  void navigate({ to: "/support/$ticketId", params: { ticketId: String(r.id) } })
                }
              >
                <List.Item.Meta
                  title={
                    <Space size={8} wrap>
                      <Typography.Text code>{r.ticket_no}</Typography.Text>
                      {cm && <Tag>{t(cm.labelKey)}</Tag>}
                      <span>{r.subject}</span>
                    </Space>
                  }
                  description={formatDateTime(r.updated_at)}
                />
                <Badge status={sm?.badge ?? "default"} text={sm ? t(sm.labelKey) : r.status} />
              </List.Item>
            );
          }}
        />
        <LoadMoreButton
          visible={tickets.hasNextPage}
          loading={tickets.isFetchingNextPage}
          onClick={() => void tickets.fetchNextPage()}
        />
      </Card>
      <CreateTicketModal open={creating} onClose={() => setCreating(false)} />
    </Space>
  );
}
