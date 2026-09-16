/**
 * Platform configuration: grouped navigation on the left + configuration risk alerts on top + grouped forms on the right; the security policy page is switch rows.
 * Super admin read/write only; env is the default layer, DB overrides take effect at once. Secrets show only "configured + last 4"; blank = unchanged.
 */

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, App, Button, Card, Form, Grid, Input, Menu, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { fontWeight, space, useFormDraft } from "@superdl/ui";
import { AttentionBar, type AttentionItem, GatedButton, PageContainer } from "@superdl/ui/components";
import { useApiErrorText } from "@superdl/ui";

import { isApiError, usePlatformConfig, useUpdatePlatformConfig } from "../../api";
import { useAdminRole } from "../../stores/auth";
import { ConfigWarning, GROUP_LABEL_KEY, Group, NAV, NavDotLegend, NavLabel, groupDotStatus } from "./-platformNav";
import { GroupPanel, useFieldLabel } from "./-platformFields";
import { EmailTestCard, RegistryTestCard, SmsTestCard } from "./-platformTestCards";
import { RISK_OFF, type RiskOffKey, SecurityPanel } from "./-platformSecurity";

export const Route = createFileRoute("/_app/platform")({
  validateSearch: (search: Record<string, unknown>): { group?: Group } => ({
    group:
      typeof search.group === "string" && search.group !== "security" && search.group in GROUP_LABEL_KEY
        ? (search.group as Group)
        : undefined,
  }),
  component: PlatformConfigPage,
});

function PlatformConfigPage() {
  const { t } = useTranslation();
  const fieldLabel = useFieldLabel();
  const errText = useApiErrorText();
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
  const role = useAdminRole();
  const isAdmin = role === "admin";
  const qc = useQueryClient();
  const { data, queryKey, isLoading, isError, error } = usePlatformConfig();
  const items = data?.items ?? [];
  const warnings: ConfigWarning[] = data?.warnings ?? [];
  const byKey = new Map(items.map((i) => [i.key, i]));
  const configDraft = useFormDraft<Record<string, string>>("platform-config");
  const [draftState, setDraftState] = useState<Record<string, string>>(() => {
    const d = configDraft.load();
    return d
      ? Object.fromEntries(Object.entries(d).filter((e): e is [string, string] => typeof e[1] === "string"))
      : {};
  });
  const [reasonOpen, setReasonOpen] = useState(false);
  const navigate = useNavigate({ from: "/platform" });
  const active: Group = Route.useSearch({ select: (s) => s.group }) ?? "security";
  const setActive = (g: Group) =>
    void navigate({
      to: "/platform",
      replace: true,
      search: (prev) => ({ ...prev, group: g === "security" ? undefined : g }),
    });
  const [originGroup, setOriginGroup] = useState<Group | null>(null);
  const [reasonForm] = Form.useForm<{ reason: string }>();
  const setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>> = (updater) => {
    setDraftState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      if (items.length > 0) {
        const persistable = Object.fromEntries(Object.entries(next).filter(([k]) => byKey.get(k)?.kind !== "secret"));
        if (Object.keys(persistable).length === 0) configDraft.clear();
        else configDraft.save(persistable);
      }
      return next;
    });
  };
  const draft = draftState;

  const update = useUpdatePlatformConfig({
    mutation: {
      onSuccess: (d) => {
        message.success(t("platform.savedCount", { count: d.updated.length }));
        setDraft({});
        setReasonOpen(false);
        reasonForm.resetFields();
        void qc.invalidateQueries({ queryKey });
      },
      onError: (e) => message.error(errText(e, t("common.saveFailed"))),
    },
  });

  const changed = Object.entries(draft).filter(([k, v]) => {
    const item = byKey.get(k);
    if (!item) return false;
    if (v === "") return item.kind !== "secret" ? item.source === "override" : false;
    if (item.kind === "secret") return true;
    return v !== (item.value ?? "");
  });
  const riskyOff = changed.filter(([k, v]) => k in RISK_OFF && v === "false");
  const dirtyGroups = new Set(changed.map(([k]) => byKey.get(k)?.group).filter((g): g is Group => g != null));
  const changedByGroup = NAV.flatMap((n) => n.groups)
    .map((g) => ({ group: g, rows: changed.filter(([k]) => byKey.get(k)?.group === g) }))
    .filter((x) => x.rows.length > 0);

  if (isError) {
    return (
      <PageContainer title={t("menu.platform")}>
        <Card>
          <Alert
            type="error"
            showIcon
            title={
              isApiError(error) && error.status === 403
                ? t("platform.adminOnly")
                : t("platform.loadFailed", { message: errText(error, t("platform.networkError")) })
            }
          />
        </Card>
      </PageContainer>
    );
  }

  const disabled = !isAdmin;
  const attentionItems: AttentionItem[] = warnings.map((w) => ({
    key: `${w.key}:${w.message}`,
    severity: w.level,
    title: w.message,
    action: (
      <Button
        size="small"
        onClick={() => {
          const g = byKey.get(w.key)?.group;
          if (g) setActive(g);
        }}
      >
        {t("platform.goTo")}
      </Button>
    ),
  }));
  const menuItems = NAV.map((n) => ({
    type: "group" as const,
    label: t(n.labelKey),
    children: n.groups.map((g) => ({
      key: g,
      label: (
        <NavLabel
          status={groupDotStatus(g, items, warnings, byKey)}
          text={t(GROUP_LABEL_KEY[g])}
          dirty={dirtyGroups.has(g)}
        />
      ),
    })),
  }));
  const panel =
    active === "security" ? (
      <SecurityPanel
        items={items.filter((i) => i.group === "security")}
        deployment={data?.deployment}
        draft={draft}
        setDraft={setDraft}
        disabled={disabled}
        byKey={byKey}
        warnings={warnings}
        onGoTo={(g) => {
          setOriginGroup("security");
          setActive(g);
        }}
      />
    ) : (
      <GroupPanel
        group={active}
        items={items.filter((i) => i.group === active)}
        draft={draft}
        setDraft={setDraft}
        disabled={disabled}
        origin={
          originGroup
            ? {
                group: originGroup,
                onBack: () => {
                  setActive(originGroup);
                  setOriginGroup(null);
                },
              }
            : undefined
        }
        extraContent={
          active === "sms" ? (
            <SmsTestCard disabled={disabled} />
          ) : active === "email" ? (
            <EmailTestCard disabled={disabled} />
          ) : active === "registry" ? (
            <RegistryTestCard disabled={disabled} />
          ) : undefined
        }
      />
    );

  return (
    <PageContainer
      title={t("menu.platform")}
      extra={
        <GatedButton
          type="primary"
          reason={isAdmin ? undefined : t("platform.adminOnlyEdit")}
          disabled={changed.length === 0}
          onClick={() => setReasonOpen(true)}
        >
          {t("settings.saveChanges", { count: changed.length })}
        </GatedButton>
      }
    >
      <Card loading={isLoading}>
        <AttentionBar items={attentionItems} style={{ marginBottom: space.lg }} />
        <div
          style={{
            display: "flex",
            gap: 24,
            alignItems: "flex-start",
            flexDirection: screens.lg ? "row" : "column",
          }}
        >
          <div style={{ width: screens.lg ? 220 : "100%", flex: "none" }}>
            <Menu
              mode={screens.lg ? "inline" : "horizontal"}
              selectedKeys={[active]}
              items={menuItems}
              onClick={(e) => {
                setOriginGroup(null);
                setActive(e.key as Group);
              }}
              style={{ width: "100%", background: "transparent" }}
            />
            <NavDotLegend />
          </div>
          <div style={{ flex: 1, minWidth: 0, width: "100%" }}>{panel}</div>
        </div>
        <Modal
          title={t("platform.confirmTitle")}
          open={reasonOpen}
          onCancel={() => setReasonOpen(false)}
          okButtonProps={{ loading: update.isPending }}
          onOk={() => {
            void (async () => {
              try {
                const { reason } = await reasonForm.validateFields();
                update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
              } catch {
                /* ignored */
              }
            })();
          }}
        >
          <Space orientation="vertical" size={space.sm} style={{ width: "100%" }}>
            {changedByGroup.map(({ group, rows }) => (
              <div key={group}>
                <Typography.Text style={{ fontWeight: fontWeight.semibold }}>
                  {t(GROUP_LABEL_KEY[group])}
                </Typography.Text>
                {rows.map(([k, v]) => {
                  const item = byKey.get(k);
                  const shown =
                    item?.kind === "secret" ? t("platform.secretMasked") : v === "" ? t("platform.clearOverride") : v;
                  return (
                    <div key={k}>
                      {fieldLabel(k)} → <b>{shown}</b>
                    </div>
                  );
                })}
              </div>
            ))}
            {riskyOff.length > 0 && (
              <Alert
                type="error"
                showIcon
                title={t("platform.riskOffTitle")}
                description={riskyOff.map(([k]) => {
                  const riskKey = (RISK_OFF as Record<string, RiskOffKey>)[k];
                  return (
                    <div key={k}>
                      {fieldLabel(k)}:{riskKey ? t(riskKey) : k}
                    </div>
                  );
                })}
              />
            )}
            <Alert type="warning" showIcon title={t("platform.instantEffect")} />
            <Form form={reasonForm} layout="vertical">
              <Form.Item
                name="reason"
                label={t("platform.reasonLabel")}
                rules={[{ required: true, min: 2, message: t("common.reasonRule") }]}
              >
                <Input.TextArea rows={2} placeholder={t("platform.reasonPlaceholder")} />
              </Form.Item>
            </Form>
          </Space>
        </Modal>
      </Card>
    </PageContainer>
  );
}
