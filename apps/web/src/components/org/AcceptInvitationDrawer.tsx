/** Review an invitation before joining: who invites, the role, and exactly what owners and admins will see (the disclosure, whose version is recorded on acceptance). */

import { drawerWidth, formatDateTime, space } from "@superdl/ui";
import { GatedButton, KeyValue, TableErrorEmpty } from "@superdl/ui/components";
import { App, Button, Checkbox, Drawer, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAcceptInvitation, useDeclineInvitation } from "../../api/mutations";
import { useOrgDisclosure, useReceivedInvitations } from "../../api/queries";

/** Disclosure items the server lists; an unknown future item is shown by its id rather than hidden. */
const DISCLOSURE_LABEL = {
  identity: "org.disclosure.item.identity",
  available_balance: "org.disclosure.item.available_balance",
  month_to_date: "org.disclosure.item.month_to_date",
  burn_rate: "org.disclosure.item.burn_rate",
  instances: "org.disclosure.item.instances",
  routed_access: "org.disclosure.item.routed_access",
} as const;

function isKnownItem(item: string): item is keyof typeof DISCLOSURE_LABEL {
  return item in DISCLOSURE_LABEL;
}

export function AcceptInvitationDrawer({ uuid, onClose }: { uuid: string | undefined; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const open = uuid !== undefined;
  const received = useReceivedInvitations({ enabled: open });
  const disclosure = useOrgDisclosure({ enabled: open });
  const [agreed, setAgreed] = useState(false);
  const close = () => {
    setAgreed(false);
    onClose();
  };
  const accept = useAcceptInvitation({
    onSuccess: () => {
      message.success(t("org.received.joined"));
      close();
    },
  });
  const decline = useDeclineInvitation({
    onSuccess: () => {
      message.success(t("org.received.declined"));
      close();
    },
  });
  const inv = (received.data ?? []).find((r) => r.uuid === uuid);
  const version = disclosure.data?.version;
  const reason = inv?.blocked
    ? t("org.received.blocked")
    : !agreed
      ? t("org.received.agreeFirst")
      : version === undefined
        ? t("org.received.loading")
        : undefined;

  return (
    <Drawer
      open={open}
      onClose={close}
      size={drawerWidth.md}
      title={inv ? t("org.received.drawerTitle", { name: inv.org_name }) : t("org.received.title")}
      footer={
        inv && (
          <Space style={{ display: "flex", justifyContent: "flex-end" }}>
            <Button loading={decline.isPending} onClick={() => decline.mutate(inv.uuid)}>
              {t("org.received.decline")}
            </Button>
            <GatedButton
              type="primary"
              reason={reason}
              loading={accept.isPending}
              onClick={() => version && accept.mutate({ uuid: inv.uuid, consentVersion: version })}
            >
              {t("org.received.accept")}
            </GatedButton>
          </Space>
        )
      }
    >
      {received.isError || disclosure.isError ? (
        <TableErrorEmpty
          isError
          compact
          onRetry={() => {
            void received.refetch();
            void disclosure.refetch();
          }}
        />
      ) : !inv ? (
        <Typography.Text type="secondary">
          {received.isLoading ? t("org.received.loading") : t("org.received.gone")}
        </Typography.Text>
      ) : (
        <Space orientation="vertical" size={space.lg} style={{ width: "100%" }}>
          <KeyValue
            columns={1}
            items={[
              { label: t("org.received.colOrg"), value: inv.org_name },
              { label: t("org.received.colRole"), value: t(`org.role.${inv.role}`) },
              { label: t("org.received.colInviter"), value: inv.invited_by },
              { label: t("org.received.colExpires"), value: formatDateTime(inv.expires_at) },
            ]}
          />
          <div>
            <Typography.Title level={5}>{t("org.disclosure.title")}</Typography.Title>
            <ul style={{ paddingInlineStart: space.lg, margin: 0 }}>
              {(disclosure.data?.items ?? []).map((item) => (
                <li key={item}>
                  <Typography.Text>{isKnownItem(item) ? t(DISCLOSURE_LABEL[item]) : item}</Typography.Text>
                </li>
              ))}
            </ul>
            <Typography.Paragraph type="secondary" style={{ marginTop: space.sm }}>
              {t("org.disclosure.never")}
            </Typography.Paragraph>
          </div>
          <Checkbox checked={agreed} disabled={Boolean(inv.blocked)} onChange={(e) => setAgreed(e.target.checked)}>
            {t("org.disclosure.agree")}
          </Checkbox>
        </Space>
      )}
    </Drawer>
  );
}
