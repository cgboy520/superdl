/** 组件体检的文案键映射与对象表列顺序。 */

import type { ClusterComponent } from "../../api";

export type ComponentKey = ClusterComponent["key"];

export const COMPONENT_LABEL = {
  nodes: "cluster.comp.nodes",
  hami: "cluster.comp.hami",
  gpu_operator: "cluster.comp.gpuOperator",
  dcgm: "cluster.comp.dcgm",
  nvidia_runtimeclass: "cluster.comp.nvidiaRuntimeclass",
  kata_runtimeclass: "cluster.comp.kataRuntimeclass",
  storage: "cluster.comp.storage",
  gateway: "cluster.comp.gateway",
  cert_manager: "cluster.comp.certManager",
  monitoring: "cluster.comp.monitoring",
} as const satisfies Record<ComponentKey, string>;

/** 组件体检判据的文案键。 */
export const COMPONENT_CRITERION = {
  nodes: "cluster.criterion.nodes",
  hami: "cluster.criterion.hami",
  gpu_operator: "cluster.criterion.gpuOperator",
  dcgm: "cluster.criterion.dcgm",
  nvidia_runtimeclass: "cluster.criterion.nvidiaRuntimeclass",
  kata_runtimeclass: "cluster.criterion.kataRuntimeclass",
  storage: "cluster.criterion.storage",
  gateway: "cluster.criterion.gateway",
  cert_manager: "cluster.criterion.certManager",
  monitoring: "cluster.criterion.monitoring",
} as const satisfies Record<ComponentKey, string>;

/** 影响面:这项不正常时平台哪条链路断。只在非正常态显示。 */
export const COMPONENT_IMPACT = {
  nodes: "cluster.impact.nodes",
  hami: "cluster.impact.hami",
  gpu_operator: "cluster.impact.gpuOperator",
  dcgm: "cluster.impact.dcgm",
  nvidia_runtimeclass: "cluster.impact.nvidiaRuntimeclass",
  kata_runtimeclass: "cluster.impact.kataRuntimeclass",
  storage: "cluster.impact.storage",
  gateway: "cluster.impact.gateway",
  cert_manager: "cluster.impact.certManager",
  monitoring: "cluster.impact.monitoring",
} as const satisfies Record<ComponentKey, string>;

/** 事实 key → 文案键。后端新增事实而前端未跟上时 metaOf 返回 undefined,原样回显 key。 */
export const FACT_LABEL = {
  ready: "cluster.fact.ready",
  unschedulable: "cluster.fact.unschedulable",
  notReady: "cluster.fact.notReady",
  pools: "cluster.fact.pools",
  scheduler: "cluster.fact.scheduler",
  devicePlugin: "cluster.fact.devicePlugin",
  allocatableGpu: "cluster.fact.allocatableGpu",
  reason: "cluster.fact.reason",
  operand: "cluster.fact.operand",
  driverVersion: "cluster.fact.driverVersion",
  operandCount: "cluster.fact.operandCount",
  exporter: "cluster.fact.exporter",
  image: "cluster.fact.image",
  sampleAgeSeconds: "cluster.fact.sampleAgeSeconds",
  present: "cluster.fact.present",
  handler: "cluster.fact.handler",
  applicableNodes: "cluster.fact.applicableNodes",
  poolNodes: "cluster.fact.poolNodes",
  runtimeClass: "cluster.fact.runtimeClass",
  kataDeploy: "cluster.fact.kataDeploy",
  count: "cluster.fact.count",
  instanceDisk: "cluster.fact.instanceDisk",
  dataDisk: "cluster.fact.dataDisk",
  listener: "cluster.fact.listener",
  address: "cluster.fact.address",
  attachedRoutes: "cluster.fact.attachedRoutes",
  controller: "cluster.fact.controller",
  components: "cluster.fact.components",
  prometheus: "cluster.fact.prometheus",
  alertmanager: "cluster.fact.alertmanager",
  scrapeTargets: "cluster.fact.scrapeTargets",
  alertsFiring: "cluster.fact.alertsFiring",
} as const;

/** 对象表列 key → 文案键。 */
export const OBJECT_COLUMN_LABEL = {
  name: "cluster.objcol.name",
  namespace: "cluster.objcol.namespace",
  ready: "cluster.objcol.ready",
  image: "cluster.objcol.image",
  reason: "cluster.objcol.reason",
  status: "cluster.objcol.status",
  pool: "cluster.objcol.pool",
  kubelet: "cluster.objcol.kubelet",
  port: "cluster.objcol.port",
  protocol: "cluster.objcol.protocol",
  attachedRoutes: "cluster.objcol.attachedRoutes",
  programmed: "cluster.objcol.programmed",
  provisioner: "cluster.objcol.provisioner",
  bindingMode: "cluster.objcol.bindingMode",
  expandable: "cluster.objcol.expandable",
  reclaim: "cluster.objcol.reclaim",
  default: "cluster.objcol.default",
  handler: "cluster.objcol.handler",
  nodeSelector: "cluster.objcol.nodeSelector",
  phase: "cluster.objcol.phase",
  node: "cluster.objcol.node",
  restarts: "cluster.objcol.restarts",
  message: "cluster.objcol.message",
  count: "cluster.objcol.count",
  lastSeen: "cluster.objcol.lastSeen",
  pressure: "cluster.objcol.pressure",
  taints: "cluster.objcol.taints",
  notAfter: "cluster.objcol.notAfter",
} as const;

/** 抽屉对象表的列顺序。 */
export type ObjectColumn = keyof typeof OBJECT_COLUMN_LABEL;

export const OBJECT_COLUMNS = {
  nodes: ["status", "pool", "kubelet", "reason"],
  hami: ["namespace", "ready", "image", "reason"],
  gpu_operator: ["namespace", "ready", "image", "reason"],
  dcgm: ["namespace", "ready", "image", "reason"],
  nvidia_runtimeclass: ["handler", "nodeSelector"],
  kata_runtimeclass: ["status", "pool", "kubelet"],
  storage: ["provisioner", "bindingMode", "expandable", "reclaim", "default"],
  gateway: ["port", "protocol", "attachedRoutes", "programmed", "reason"],
  cert_manager: ["namespace", "ready", "image", "reason"],
  monitoring: ["namespace", "ready", "image", "reason"],
} as const satisfies Record<ComponentKey, readonly ObjectColumn[]>;

/** 深探两张表的列:现场对象 / Warning 事件。节点项的现场表复用同一组件,列不同。 */
export const LIVE_POD_COLUMNS = [
  "namespace",
  "phase",
  "node",
  "reason",
  "restarts",
] as const satisfies readonly ObjectColumn[];
export const LIVE_EVENT_COLUMNS = ["reason", "message", "count", "lastSeen"] as const satisfies readonly ObjectColumn[];
