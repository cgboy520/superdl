/** 运行中实例的主动作「连接 ▾」:复制 SSH 命令 / 打开 JupyterLab / 连接信息 / 实例监控。access 只在菜单打开后拉取;失败在菜单内给重试。 */

import { CodeOutlined, CopyOutlined, DownOutlined, LineChartOutlined, LinkOutlined } from "@ant-design/icons";
import type { InstanceOut } from "@superdl/api-client";
import { useNavigate } from "@tanstack/react-router";
import { App, Button, Dropdown, type MenuProps } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useInstanceAccess } from "../api/queries";

export function ConnectMenu({ instance, size = "small" }: { instance: InstanceOut; size?: "small" | "middle" }) {
  const { t } = useTranslation(["web", "shared"]);
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const access = useInstanceAccess(instance.uuid, { enabled: open });
  const sshCommand = access.data?.ssh_command;
  const jupyterUrl = access.data?.jupyter_url;

  const items: NonNullable<MenuProps["items"]> = [
    {
      key: "ssh",
      icon: <CopyOutlined />,
      label: access.isError
        ? t("common.loadFailed", { ns: "shared" })
        : access.isPending
          ? t("connect.loading")
          : t("connect.copySsh"),
      disabled: !sshCommand,
    },
    {
      key: "jupyter",
      icon: <CodeOutlined />,
      label: t("connect.openJupyter"),
      disabled: !jupyterUrl,
    },
    { type: "divider" },
    { key: "access", icon: <LinkOutlined />, label: t("connect.accessInfo") },
    { key: "metrics", icon: <LineChartOutlined />, label: t("instances.monitorLink") },
    ...(access.isError
      ? [{ type: "divider" as const }, { key: "retry", label: t("common.retry", { ns: "shared" }) }]
      : []),
  ];

  return (
    <Dropdown
      open={open}
      onOpenChange={setOpen}
      menu={{
        items,
        onClick: ({ key }) => {
          if (key === "ssh" && sshCommand) {
            void navigator.clipboard.writeText(sshCommand).then(() => message.success(t("common.copied")));
          } else if (key === "jupyter" && jupyterUrl) {
            window.open(jupyterUrl, "_blank", "noopener,noreferrer");
          } else if (key === "access") {
            void navigate({ to: "/instances/$uuid", params: { uuid: instance.uuid }, search: { tab: "access" } });
          } else if (key === "metrics") {
            void navigate({ to: "/instances/$uuid", params: { uuid: instance.uuid }, search: { tab: "metrics" } });
          } else if (key === "retry") {
            void access.refetch();
            return;
          }
          setOpen(false);
        },
      }}
    >
      <Button type="primary" size={size} icon={<LinkOutlined />}>
        {t("connect.label")} <DownOutlined />
      </Button>
    </Dropdown>
  );
}
