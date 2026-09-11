/**
 * 平台配置:左侧分组导航 + 顶部配置风险告警 + 右侧分组表单;安全策略页是开关行。
 * 仅超级管理员可读写;env 为默认值层,DB 覆盖即时生效。secret 只显示「已配置 + 尾 4 位」,留空 = 不变。
 */

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Alert, App, Button, Card, Form, Grid, Input, Menu, Modal, Space, Tooltip } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useFormDraft } from "@superdl/ui";
import { PageContainer } from "@superdl/ui/components";
import { useApiErrorText } from "@superdl/ui";

import { isApiError, usePlatformConfig, useUpdatePlatformConfig } from "../../api";
import { useAdminRole } from "../../stores/auth";
import { ConfigWarning, GROUP_LABEL_KEY, Group, NAV, NavLabel, groupDotColor } from "./-platformNav";
import { FIELD_LABELS, GroupPanel } from "./-platformFields";
import { RegistryTestCard, SmsTestCard } from "./-platformTestCards";
import { RISK_OFF, SecurityPanel } from "./-platformSecurity";

export const Route = createFileRoute("/_app/platform")({
  // group:当前配置分组入 URL(默认 security 剥离),可直链
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
  // 非 secret 字段变更草稿(sessionStorage);secret 禁入草稿
  const configDraft = useFormDraft<Record<string, string>>("platform-config");
  const [draftState, setDraftState] = useState<Record<string, string>>(() => {
    const d = configDraft.load();
    // 草稿值收窄回 Record<string, string>
    return d
      ? Object.fromEntries(
          Object.entries(d).filter((e): e is [string, string] => typeof e[1] === "string"),
        )
      : {};
  });
  const [reasonOpen, setReasonOpen] = useState(false);
  // 当前分组 = URL(?group=),默认 security
  const navigate = useNavigate({ from: "/platform" });
  const active: Group = Route.useSearch({ select: (s) => s.group }) ?? "security";
  const setActive = (g: Group) =>
    void navigate({ to: "/platform", replace: true, search: (prev) => ({ ...prev, group: g === "security" ? undefined : g }) });
  // 「前往」跳入的来源分组(回链)
  const [originGroup, setOriginGroup] = useState<Group | null>(null);
  const [reasonForm] = Form.useForm<{ reason: string }>();
  // 配置项到达后清洗草稿:剔除 secret 字段与已下线的键
  const [draftSanitized, setDraftSanitized] = useState(false);
  if (!draftSanitized && items.length > 0) {
    setDraftSanitized(true);
    setDraftState((d) =>
      Object.fromEntries(
        Object.entries(d).filter(([k]) => {
          const item = byKey.get(k);
          return item != null && item.kind !== "secret";
        }),
      ),
    );
  }
  // 每次变更同步写草稿(只落非 secret 字段)
  const setDraft: React.Dispatch<React.SetStateAction<Record<string, string>>> = (updater) => {
    setDraftState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      if (items.length > 0) {
        const persistable = Object.fromEntries(
          Object.entries(next).filter(([k]) => byKey.get(k)?.kind !== "secret"),
        );
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
  const menuItems = NAV.map((n) => ({
    type: "group" as const,
    label: t(n.labelKey),
    children: n.groups.map((g) => ({
      key: g,
      label: (
        <NavLabel color={groupDotColor(g, items, warnings, byKey)} text={t(GROUP_LABEL_KEY[g])} />
      ),
    })),
  }));
  const panel =
    active === "security" ? (
      <SecurityPanel
        items={items.filter((i) => i.group === "security")}
        draft={draft}
        setDraft={setDraft}
        disabled={disabled}
        byKey={byKey}
        warnings={warnings}
        // 「前往」带出来源分组
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
        <Tooltip title={isAdmin ? "" : t("platform.adminOnlyEdit")}>
          <Button
            type="primary"
            disabled={disabled || changed.length === 0}
            onClick={() => setReasonOpen(true)}
          >
            {t("settings.saveChanges", { count: changed.length })}
          </Button>
        </Tooltip>
      }
    >
    <Card loading={isLoading}>
      {warnings.length > 0 && (
        <Space orientation="vertical" size={8} style={{ width: "100%", marginBottom: 16 }}>
          {warnings.map((w) => (
            <Alert
              key={`${w.key}:${w.message}`}
              type={w.level}
              showIcon
              title={w.message}
              action={
                <Button
                  size="small"
                  onClick={() => {
                    const g = byKey.get(w.key)?.group;
                    if (g) setActive(g);
                  }}
                >
                  {t("platform.goTo")}
                </Button>
              }
            />
          ))}
        </Space>
      )}
      <div
        style={{
          display: "flex",
          gap: 24,
          alignItems: "flex-start",
          flexDirection: screens.lg ? "row" : "column",
        }}
      >
        <Menu
          mode={screens.lg ? "inline" : "horizontal"}
          selectedKeys={[active]}
          items={menuItems}
          // 手动切分组作废来源回链
          onClick={(e) => {
            setOriginGroup(null);
            setActive(e.key as Group);
          }}
          style={
            screens.lg
              ? { width: 220, flex: "none", background: "transparent" }
              : { width: "100%", flex: "none", background: "transparent" }
          }
        />
        <div style={{ flex: 1, minWidth: 0, width: "100%" }}>{panel}</div>
      </div>
      <Modal
        title={t("platform.confirmTitle")}
        open={reasonOpen}
        onCancel={() => setReasonOpen(false)}
        okButtonProps={{ loading: update.isPending }}
        onOk={async () => {
          try {
            const { reason } = await reasonForm.validateFields();
            update.mutate({ data: { updates: Object.fromEntries(changed), reason } });
          } catch {
            /* 校验失败:antd 已给红字 */
          }
        }}
      >
        <Space orientation="vertical" size={8} style={{ width: "100%" }}>
          {changed.map(([k, v]) => {
            const item = byKey.get(k);
            const shown =
              item?.kind === "secret" ? t("platform.secretMasked") : v === "" ? t("platform.clearOverride") : v;
            return (
              <div key={k}>
                {FIELD_LABELS[k] ?? k} → <b>{shown}</b>
              </div>
            );
          })}
          {riskyOff.length > 0 && (
            <Alert
              type="error"
              showIcon
              title={t("platform.riskOffTitle")}
              description={riskyOff.map(([k]) => (
                <div key={k}>
                  {FIELD_LABELS[k] ?? k}:{RISK_OFF[k]}
                </div>
              ))}
            />
          )}
          <Alert
            type="warning"
            showIcon
            title={t("platform.instantEffect")}
          />
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

