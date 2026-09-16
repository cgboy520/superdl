/** 节点池标识 → 文案键(与后端 pool_label 一致;admin.json nodes.pool*)。 */
export const POOL_LABEL_KEY = {
  kata: "nodes.poolKata",
  hami: "nodes.poolHami",
  mig: "nodes.poolMig",
  cpu: "nodes.poolCpu",
} as const;

/** 已登记池标识的联合类型。 */
export type Pool = keyof typeof POOL_LABEL_KEY;

/** 可在线互切的池(与后端 core/gpu_adapter.SWITCHABLE_POOLS 同口径);cpu 是无卡机的物理属性。 */
export const SWITCHABLE_POOLS = ["kata", "hami", "mig"] as const;
export type SwitchablePool = (typeof SWITCHABLE_POOLS)[number];

/** 支持 MIG 硬件切分的家族(与后端 core/gpu_models.MIG_CAPABLE_FAMILIES 同表);
 *  canonical 取 "-" 前一段比对,未识别一律 false。 */
const MIG_CAPABLE_FAMILIES = new Set(["A100", "A800", "A30", "H100", "H800", "H200", "H20", "B200", "GB200"]);

export function supportsMig(canonical: string | undefined): boolean {
  if (!canonical) return false;
  return MIG_CAPABLE_FAMILIES.has(canonical.split("-")[0] ?? "");
}

/** 支持整卡直通的数据中心家族(与后端 core/gpu_models.PASSTHROUGH_CAPABLE_FAMILIES 同表);
 *  RTX 独立卡按前缀放行,Grace 超级芯片的集成 GPU(GB10 / GB200)绑不了 vfio-pci,未识别一律 false。 */
const PASSTHROUGH_CAPABLE_FAMILIES = new Set([
  "A10",
  "A16",
  "A30",
  "A40",
  "A100",
  "A800",
  "B200",
  "H20",
  "H100",
  "H200",
  "H800",
  "L4",
  "L20",
  "L40",
  "L40S",
  "T4",
  "V100",
]);

export function supportsPassthrough(canonical: string | undefined): boolean {
  if (!canonical) return false;
  const family = canonical.split("-")[0] ?? "";
  return family.startsWith("RTX") || PASSTHROUGH_CAPABLE_FAMILIES.has(family);
}
