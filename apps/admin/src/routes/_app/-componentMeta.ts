/** Locale key mapping and object table column order of the component health checks. */

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

/** Locale keys of the component health criteria. */
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

/** Impact: which platform chain breaks when this item is unhealthy. Shown only in non-healthy states. */
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

/** Fact key → locale key. When the backend adds a fact the frontend has not caught up with, metaOf returns undefined and the key is echoed raw. */
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

/** Object table column key → locale key. */
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

/** Column order of the drawer object table. */
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

/** Columns of the two deep-probe tables: live objects / Warning events. The node item's live table reuses the same component with different columns. */
export const LIVE_POD_COLUMNS = [
  "namespace",
  "phase",
  "node",
  "reason",
  "restarts",
] as const satisfies readonly ObjectColumn[];
export const LIVE_EVENT_COLUMNS = ["reason", "message", "count", "lastSeen"] as const satisfies readonly ObjectColumn[];
