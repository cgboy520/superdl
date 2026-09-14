/** 组件诊断抽屉(/cluster/<key>):嵌套在集群页里的遮罩路由。
 *  是真路由所以可直链、可转达、浏览器后退即关;渲染成 Drawer 所以背景的体检网格不丢。
 *  页容器由父路由 cluster.tsx 自持,本文件是遮罩层,不带 PageContainer
 *  (scripts/check-page-skeleton.py 对本文件有显式豁免)。 */

import { createFileRoute, useNavigate, useParams } from "@tanstack/react-router";
import { Drawer } from "antd";
import { useTranslation } from "react-i18next";

import { componentHealthMap, drawerWidth, formatDateTime } from "@superdl/ui";
import { EntityHeader, StatusTag, TableErrorEmpty } from "@superdl/ui/components";

import { useClusterStatus } from "../../api";
import { ComponentDrawerBody } from "./-ComponentDrawer";
import { COMPONENT_LABEL, type ComponentKey } from "./-componentMeta";

export const Route = createFileRoute("/_app/cluster/$component")({
  component: ComponentDrawerRoute,
});

function ComponentDrawerRoute() {
  const { t } = useTranslation(["admin", "shared"]);
  const navigate = useNavigate();
  const { component: key } = useParams({ from: "/_app/cluster/$component" });
  const { data, isError } = useClusterStatus();
  const close = () => void navigate({ to: "/cluster" });
  const component = data?.components.find((c) => c.key === (key as ComponentKey));

  return (
    <Drawer
      open
      onClose={close}
      placement="right"
      size={drawerWidth.lg}
      mask={{ closable: true }}
      destroyOnHidden
      title={
        component && (
          <EntityHeader
            size="drawer"
            name={t(COMPONENT_LABEL[component.key])}
            status={<StatusTag map={componentHealthMap} value={component.state} variant="badge" icon />}
            subtitle={
              data?.probed_at
                ? t("cluster.probedAt", { time: formatDateTime(data.probed_at) })
                : t("cluster.neverProbed")
            }
          />
        )
      }
    >
      {component ? (
        <ComponentDrawerBody component={component} probedAt={data?.probed_at ?? null} />
      ) : (
        <TableErrorEmpty compact isError={isError}>
          {t("cluster.componentNotFound")}
        </TableErrorEmpty>
      )}
    </Drawer>
  );
}
