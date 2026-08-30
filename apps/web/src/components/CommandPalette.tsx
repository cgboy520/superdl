/** Cmd+K 命令面板(cmdk):静态导航(控制台各页 + 帮助)+ 实例缓存模糊匹配 + 快捷动作。
 *  壳(热键/事件总线/Modal/cmdk 骨架)在 @superdl/ui/components 的 CommandPaletteShell,这里只组装数据。
 *  触发:顶栏触发器(派发自定义事件)或全局 ⌘K / Ctrl+K;
 *  实例分组只在已有缓存数据时渲染(空分组壳内不渲染)。 */

import { CloudServerOutlined } from "@ant-design/icons";
import { fontSize, instanceStatusMap, metaOf } from "@superdl/ui";
import { COMMAND_KBD_HINT, CommandPaletteShell, type CommandPaletteGroup } from "@superdl/ui/components";
import { useNavigate } from "@tanstack/react-router";
import { Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useInstances } from "../api/queries";
import { CONSOLE_NAV } from "./layout/consoleNav";

export const COMMAND_PALETTE_OPEN_EVENT = "superdl:command-palette-open";

export { COMMAND_KBD_HINT };

export function CommandPalette() {
  const { t } = useTranslation(["web", "shared"]);
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  // 实例数据只取已缓存/打开后才拉(首 100 条);无缓存时实例分组不渲染
  const { data: instances } = useInstances({ enabled: open });

  const groups: CommandPaletteGroup[] = [
    {
      heading: t("command.groupPages"),
      items: [
        ...CONSOLE_NAV.map((n) => ({
          key: n.key,
          label: t(n.labelKey),
          keywords: [n.key.slice(1)],
          run: () => void navigate({ to: n.key }),
        })),
        {
          key: "/help",
          label: t("topbar.help"),
          keywords: ["help", "faq"],
          run: () => void navigate({ to: "/help" }),
        },
      ],
    },
    {
      heading: t("command.groupInstances"),
      items: (instances ?? []).map((i) => {
        const meta = metaOf(instanceStatusMap, i.status);
        return {
          key: i.uuid,
          value: `${i.name} ${i.uuid}`,
          keywords: [i.name, i.uuid],
          run: () => void navigate({ to: "/instances/$uuid", params: { uuid: i.uuid } }),
          label: (
            <>
              <CloudServerOutlined />
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{i.name}</span>
              <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
                {meta ? t(meta.labelKey) : i.status} · {i.spec["gpu_model"] as string} × {i.gpu_count}
              </Typography.Text>
            </>
          ),
        };
      }),
    },
    {
      heading: t("command.groupActions"),
      items: [
        {
          key: "rent",
          label: t("command.actionRent"),
          keywords: ["rent", "market", "new", "instance"],
          run: () => void navigate({ to: "/market" }),
        },
        {
          key: "recharge",
          label: t("command.actionRecharge"),
          keywords: ["recharge", "billing", "topup", "chongzhi"],
          run: () => void navigate({ to: "/billing" }),
        },
        {
          key: "ticket",
          label: t("command.actionTicket"),
          keywords: ["ticket", "support", "gongdan"],
          run: () => void navigate({ to: "/support", search: { new: "1" } }),
        },
      ],
    },
  ];

  return (
    <CommandPaletteShell
      open={open}
      onOpenChange={setOpen}
      openEventName={COMMAND_PALETTE_OPEN_EVENT}
      label={t("command.trigger")}
      noResultsText={t("command.noResults")}
      hintText={t("command.hint")}
      hintExtraText={t("command.hintShortcuts")}
      groups={groups}
    />
  );
}
