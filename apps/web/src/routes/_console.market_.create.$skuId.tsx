/**
 * 创建实例:单栏卡片流(计费方式/已选规格/镜像/数据盘/SSH/名称)+ 底部结算条;经济档需知情同意。
 * 数据盘「新建」为行内直建:提交时先建盘再建实例;建盘成功而实例失败须提示盘已计费。
 *
 * `?workload=service` 走同一条卡片流的服务形态:镜像/SSH 两张卡换成「容器」「对外服务」,
 * SSH 降级成名称卡里的可选项。分叉只在卡片与提交体上,规格/数据盘/结算条/幂等键全部复用 ——
 * 拆成两个页面会让「换规格」「余额不足去充值」这些闭环各写一遍。
 */

import { isApiError, type DiskOut, type InstanceOut, type SkuMarketOut } from "@superdl/api-client";
import { compareAmounts, diskDailyEstimate, formatSizeGb, GPU_COUNT_STEPS, idemKeyOf, mulPrice, skuVariant } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Alert,
  App,
  Button,
  Card,
  Cascader,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Skeleton,
  Slider,
  Space,
  Table,
  Tabs,
  Tooltip,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { useFormat } from "../lib/format";
import { useApiErrorText } from "../lib/apiError";
import { useAddSshKey, useCreateDisk, useCreateInstance } from "../api/mutations";
import { useDisks, useImages, usePolicies, useSkus, useSshKeys, useWallet } from "../api/queries";
import { ChipRow } from "../components/ChipRow";
import { CheckoutBar } from "../components/CheckoutBar";
import { DataErrorAlert } from "../components/QueryState";
import { BillingModeCard, skuColumns } from "../components/skuTable";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/market_/create/$skuId")({
  validateSearch: (search: Record<string, unknown>): { gpus?: number; workload?: "service" } => {
    // 市场页带入的 GPU 数量(可改)与形态(缺省 = 开发机)
    const g = Number(search.gpus);
    const out: { gpus?: number; workload?: "service" } = {};
    if (Number.isInteger(g) && g >= 1 && g <= 8) out.gpus = g;
    if (search.workload === "service") out.workload = "service";
    return out;
  },
  beforeLoad: requireAuth,
  component: CreatePage,
});

/** 与后端 schemas._ENV_NAME_RE 同源:容器环境变量名的形态 */
const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
/** 与后端 _RESERVED_ENV_PREFIXES / _RESERVED_ENV_NAMES 同源:平台自己往容器里注入的名段 */
const RESERVED_ENV_PREFIXES = ["JUPYTER_", "SUPERDL_"];
const RESERVED_ENV_NAMES = ["AUTHORIZED_KEYS"];
/** 与后端 RESERVED_SERVICE_PORTS 同源:22 = sshd,8888 = JupyterLab */
const RESERVED_SERVICE_PORTS = [22, 8888];

/**
 * 引用是否钉死到具体版本(与后端 core.registry.is_pinned_image_ref 同源判定)。
 * 服务容器的 restartPolicy 是 Always,可变 tag 会让某次半夜重启悄悄换掉线上版本;
 * 后端是硬闸,这里只是提前一步给反馈,判据必须与它一致(不写 tag = 隐含 latest,同样不算钉死)。
 */
function isPinnedImageRef(ref: string): boolean {
  if (ref.includes("@sha256:")) return true;
  // 冒号也可能是仓库主机的端口(registry:5000/img),tag 只看最后一段路径
  const last = ref.split("/").pop() ?? "";
  const colon = last.lastIndexOf(":");
  return colon > 0 && last.slice(colon + 1) !== "latest";
}

interface ArgRow {
  id: string;
  value: string;
}
interface EnvRow {
  id: string;
  name: string;
  value: string;
  secret: boolean;
}

function defaultDiskName(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `data-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function CreatePage() {
  const { t } = useTranslation(["web", "shared"]);
  // 「镜像必须钉死版本」这句话的事实源在后端 messages.py,前端不另写一份
  const { t: tErr } = useTranslation("errors");
  const fmt = useFormat();
  const { formatHourlyPrice } = fmt;
  const { skuId } = Route.useParams();
  const { gpus: gpusFromMarket, workload } = Route.useSearch();
  // 服务形态:换掉镜像/SSH 两张卡,其余卡片与结算逻辑逐字复用
  const isService = workload === "service";
  const navigate = useNavigate();
  const { message } = App.useApp();

  const { data: skus, isLoading: skusLoading, isError: skusError, refetch: refetchSkus } = useSkus();
  const sku = (skus ?? []).find((s) => s.id === Number(skuId));

  const imagesQ = useImages();
  const keysQ = useSshKeys();
  const disksQ = useDisks();
  const { data: images } = imagesQ;
  const { data: keys } = keysQ;
  const { data: disks } = disksQ;
  const { data: wallet } = useWallet();
  const { data: policies } = usePolicies();

  const [gpuCount, setGpuCount] = useState(gpusFromMarket ?? 1);
  // 后端契约:CPU 规格(max_gpus_per_instance=0)只收 gpu_count=0,GPU 规格只收 1..max
  const [imageTab, setImageTab] = useState<"platform" | "custom">("platform");
  const [platformImage, setPlatformImage] = useState<string[]>();
  const [customImage, setCustomImage] = useState("");
  const [diskMode, setDiskMode] = useState<"none" | "new" | "existing">("none");
  const [newDiskName, setNewDiskName] = useState(defaultDiskName);
  const [newDiskGb, setNewDiskGb] = useState(100);
  const [existingDiskId, setExistingDiskId] = useState<number>();
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [name, setName] = useState("");
  // ---- 服务形态专属 ----
  const [serviceImage, setServiceImage] = useState("");
  const [command, setCommand] = useState("");
  const [argRows, setArgRows] = useState<ArgRow[]>([]);
  const [envRows, setEnvRows] = useState<EnvRow[]>([]);
  const [servicePort, setServicePort] = useState<number | null>(null);
  const [healthPath, setHealthPath] = useState("");
  const [requireApiKey, setRequireApiKey] = useState(true);
  const [withSsh, setWithSsh] = useState(false);
  const [ecoOpen, setEcoOpen] = useState(false);
  const [ecoChecked, setEcoChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [keyForm] = Form.useForm<{ name: string; public_key: string }>();
  // 幂等键 = 本次挂载的 nonce + 参数快照:同参数重放同键;新进入本页才是新单
  const [formNonce] = useState(() => crypto.randomUUID());

  const cascade = useMemo(() => {
    const tree: Record<string, Record<string, Record<string, Record<string, string>>>> = {};
    for (const img of images ?? []) {
      // CPU 向镜像(如 DataScience)的 cuda_version 不是版本号,直接原样显示,别拼成「CUDA CPU」
      const cudaLabel = /^\d/.test(img.cuda_version)
        ? `CUDA ${img.cuda_version}`
        : img.cuda_version;
      ((((tree[img.framework] ??= {})[img.framework_version] ??= {})[img.python_version] ??= {})[
        cudaLabel
      ] = img.image_ref);
    }
    return Object.entries(tree).map(([fw, versions]) => ({
      value: fw,
      label: fw,
      children: Object.entries(versions).map(([ver, pys]) => ({
        value: ver,
        label: ver,
        children: Object.entries(pys).map(([py, cudas]) => ({
          value: py,
          label: `Python ${py}`,
          children: Object.entries(cudas).map(([cuda, ref]) => ({ value: ref, label: cuda })),
        })),
      })),
    }));
  }, [images]);

  const pageTitle = isService ? t("create.serviceTitle") : t("create.title");
  const errText = useApiErrorText();
  const create = useCreateInstance({
    // 错误统一在本页 doCreate 的 catch 里出(避免 NO_CAPACITY 引导与全局错误弹两条)
    silentError: true,
    onSuccess: (data) => {
      const inst = data as InstanceOut;
      message.success(
        isService ? t("create.deploying", { name: inst.name }) : t("create.creating", { name: inst.name }),
      );
      void navigate({ to: "/instances" });
    },
  });
  const createDisk = useCreateDisk();
  const addKey = useAddSshKey({
    onSuccess: (key) => {
      message.success(t("create.keyAdded"));
      keyForm.resetFields();
      setKeyIds((ids) => (ids.includes(key.id) ? ids : [...ids, key.id]));
    },
  });

  // 规格三态:加载中骨架 / 加载失败可重试(绝不能渲染成「已下架」) / 真不存在才提示下架
  if (skusError && !skus) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {pageTitle}
        </Typography.Title>
        <DataErrorAlert onRetry={() => void refetchSkus()} />
      </Space>
    );
  }
  if (!skus) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {pageTitle}
        </Typography.Title>
        <Card>
          <Skeleton active paragraph={{ rows: 6 }} loading={skusLoading} />
        </Card>
      </Space>
    );
  }
  if (!sku) {
    return (
      <Space orientation="vertical" size={16} style={{ width: "100%" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {pageTitle}
        </Typography.Title>
        <Alert
          type="warning"
          showIcon
          title={t("create.skuMissing")}
          action={
            <Link to="/market">
              <Button size="small">{t("instances.goMarket")}</Button>
            </Link>
          }
        />
      </Space>
    );
  }

  // CPU 规格:不带卡,提交 gpu_count: 0;价格是整机时价,不乘卡数
  const isCpu = sku.tier === "cpu";
  const gpus = isCpu ? 0 : gpuCount;
  const priceUnits = isCpu ? 1 : gpuCount;

  const diskPriceGbMonth = policies?.disk_price_gb_month;
  const diskGb =
    diskMode === "new"
      ? newDiskGb
      : diskMode === "existing"
        ? ((disks ?? []).find((d) => d.id === existingDiskId)?.size_gb ?? 0)
        : 0;
  // 「约 ¥X/日」为展示层估算(月价/30,BigInt 禁浮点);入账以后端日结为准
  const diskDaily = diskDailyEstimate(diskPriceGbMonth, diskGb);
  const hourlyTotal = mulPrice(sku.price_hourly, priceUnits);
  // BigInt 精确比较,禁浮点(与后端 require_balance_at_least 同口径:1 小时 GPU 费)。
  // 三态处理:未就绪 ≠ 余额为 0。
  const balanceReady = wallet != null;
  const enough = balanceReady && compareAmounts(wallet.balance, hourlyTotal) >= 0;

  const imageRef = isService
    ? serviceImage.trim()
    : imageTab === "platform"
      ? platformImage?.[3]
      : customImage.trim();

  // 环境变量:名字非空的行才算数;同名以最后一行为准,但重名会先被下面的 envError 拦下
  const envEntries = envRows
    .map((r) => ({ ...r, name: r.name.trim() }))
    .filter((r) => r.name !== "");
  const envDict = Object.fromEntries(envEntries.map((r) => [r.name, r.value]));
  const envSecretKeys = envEntries.filter((r) => r.secret).map((r) => r.name);
  // 启动命令按空格拆成 exec 形式:容器 command 不经 shell,整串带空格会被当成一个可执行文件名
  const commandList = command.trim() ? command.trim().split(/\s+/) : [];
  const argList = argRows.map((r) => r.value.trim()).filter((v) => v !== "");

  /** 单行环境变量的错误(与后端 model_validator 同款判据),没有则返回 null */
  const envError = (row: EnvRow): string | null => {
    const key = row.name.trim();
    if (key === "") return null;
    if (!ENV_NAME_RE.test(key)) return t("create.envNameInvalid");
    if (RESERVED_ENV_NAMES.includes(key) || RESERVED_ENV_PREFIXES.some((pre) => key.startsWith(pre))) {
      return t("create.envNameReserved");
    }
    if (envEntries.filter((r) => r.name === key).length > 1) return t("create.envNameDuplicate");
    return null;
  };

  /**
   * 服务形态的提交前置条件。返回一句可读的原因(挂在禁用按钮的 tooltip 上),
   * 不返回文案 key —— i18next-cli 的 extract 看不见动态键,会把它们当未引用删掉。
   */
  const serviceIssue = ((): string | null => {
    if (!isService) return null;
    if (!imageRef) return t("create.serviceNeedsImage");
    if (!isPinnedImageRef(imageRef)) return tErr("orchestrator.imageRefNotPinned");
    if (servicePort == null) return t("create.servicePortRequired");
    if (RESERVED_SERVICE_PORTS.includes(servicePort)) return t("create.servicePortReserved");
    if (healthPath.trim() !== "" && !healthPath.trim().startsWith("/")) {
      return t("create.healthPathSlash");
    }
    const bad = envRows.map(envError).find((e) => e != null);
    if (bad != null) return bad;
    // 开了 SSH 却一把公钥都不选 = 建出一台谁也登不上去的实例(后端同款校验)
    if (withSsh && keyIds.length === 0) return t("create.serviceNeedsKey");
    return null;
  })();

  const canSubmit = isService ? serviceIssue == null : Boolean(imageRef) && keyIds.length > 0;

  const doCreate = async () => {
    setSubmitting(true);
    // 幂等键由本次提交的参数派生,失败时不轮换:响应丢失后重提不会开出第二台;
    // 参数变了键随之变,不会被上一次的结果遮住
    const idempotencyKey = idemKeyOf("inst", [
      formNonce,
      sku.id,
      gpus,
      imageRef ?? "",
      (isService && !withSsh ? [] : [...keyIds].sort((a, b) => a - b)).join(","),
      name || null,
      diskMode,
      existingDiskId ?? null,
      diskMode === "new" ? newDiskName.trim() : null,
      diskMode === "new" ? newDiskGb : null,
      // 服务参数也进快照:改了端口/环境变量再提交必须是一张新单,不能被上一次的结果遮住
      isService ? "service" : "dev",
      isService ? servicePort : null,
      isService ? commandList.join(" ") : null,
      isService ? argList.join("\u0000") : null,
      isService ? JSON.stringify(envDict) : null,
      isService ? envSecretKeys.join(",") : null,
      isService ? healthPath.trim() : null,
      isService ? String(requireApiKey) : null,
      isService ? String(withSsh) : null,
    ]);
    try {
      let diskId: number | null = diskMode === "existing" ? (existingDiskId ?? null) : null;
      if (diskMode === "new") {
        let disk: DiskOut;
        try {
          disk = (await createDisk.mutateAsync({
            body: {
              name: newDiskName.trim() || defaultDiskName(),
              size_gb: newDiskGb,
            },
            // 与实例同一个参数快照派生:建盘成功但建实例失败时重提,不会再多一块盘
            idempotencyKey,
          })) as DiskOut;
        } catch {
          return; // 建盘失败:useApiMutation 已弹错误,直接终止
        }
        diskId = disk.id;
      }
      try {
        await create.mutateAsync({
          body: {
            sku_id: sku.id,
            gpu_count: gpus,
            image_ref: imageRef ?? "",
            // 服务实例取消勾选 SSH 后不该还带着公钥:不开 sshd 的容器注入 authorized_keys 没意义
            ssh_key_ids: isService && !withSsh ? [] : keyIds,
            name: name || null,
            data_disk_id: diskId,
            // dev 形态一个服务字段都不能出现:后端按 model_fields_set 判「显式传了」,
            // 传了就是 422(静默忽略会让用户以为启动命令生效了,而实例跑的是镜像原样)
            ...(isService
              ? {
                  workload_type: "service" as const,
                  container_command: commandList.length > 0 ? commandList : null,
                  container_args: argList.length > 0 ? argList : null,
                  env: envEntries.length > 0 ? envDict : null,
                  env_secret_keys: envSecretKeys.length > 0 ? envSecretKeys : null,
                  service_port: servicePort,
                  health_path: healthPath.trim() || null,
                  require_api_key: requireApiKey,
                  with_ssh: withSsh,
                }
              : {}),
          },
          idempotencyKey,
        });
      } catch (err) {
        // 刻意不换键:失败可能只是响应丢了而实例已经建好,换键会让重试开出第二台机器
        // silentError 模式下这里统一出提示:库存不足给换档引导,其余给错误原文
        if (isApiError(err) && err.code === "NO_CAPACITY") {
          message.warning(t("copy.noCapacityGuide"), 6);
        } else {
          message.error(errText(err));
        }
        if (diskMode === "new" && diskId != null) {
          message.warning(t("copy.diskCreatedButInstanceFailed"), 6);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  const submit = () => {
    // 经济档 = 落 hami 池的共享(软切分超卖);mig 池的共享是硬切分,不弹此 modal
    if (skuVariant(sku.tier, sku.pool_label) === "shared_hami") {
      setEcoOpen(true);
      return;
    }
    void doCreate();
  };

  const gpuOptions = Array.from({ length: sku.max_gpus_per_instance }, (_, i) => i + 1).filter(
    (n) => GPU_COUNT_STEPS.includes(n) || n === sku.max_gpus_per_instance,
  );

  const columns = skuColumns({ fmt, t, cpu: isCpu });

  // 开发机形态是一整张卡;服务形态挂在「同时开放 SSH」勾选项下面 —— 同一块 UI,别写两遍
  const sshKeyPicker = keysQ.isError ? (
    // SSH key 查询失败绝不伪装成「你还没有密钥」(老客户会看到添加表单,
    // 提交又被 sshKeyDuplicate 拒绝——购买路径硬停)
    <DataErrorAlert onRetry={() => void keysQ.refetch()} />
  ) : (keys ?? []).length === 0 ? (
    <Space orientation="vertical" size={12} style={{ width: "100%" }}>
      <Alert type="warning" showIcon title={t("copy.sshKeyOnly")} />
      <Form
        form={keyForm}
        layout="inline"
        onFinish={(v) => addKey.mutate({ name: v.name, public_key: v.public_key })}
      >
        <Form.Item name="name" rules={[{ required: true, message: t("create.keyNameRequired") }]}>
          <Input placeholder={t("create.keyNamePlaceholder")} style={{ width: 160 }} />
        </Form.Item>
        <Form.Item
          name="public_key"
          rules={[{ required: true, message: t("create.keyContentRequired") }]}
          style={{ flex: 1 }}
        >
          <Input placeholder={t("create.keyPlaceholder")} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={addKey.isPending}>
            {t("create.addKey")}
          </Button>
        </Form.Item>
      </Form>
    </Space>
  ) : (
    <Checkbox.Group
      value={keyIds}
      onChange={(v) => setKeyIds(v as number[])}
      options={(keys ?? []).map((k) => ({
        value: k.id,
        label: `${k.name}(${k.fingerprint.slice(0, 20)}…)`,
      }))}
    />
  );

  return (
    // 不用 Space:其 ant-space-item 包装会让 sticky 结算条的包含块只剩自身高度
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {pageTitle}
      </Typography.Title>

      <BillingModeCard />

      <Alert type="info" showIcon title={t("copy.instanceDiskLocalNotice")} />

      <Card title={t("create.selectedSpec")} extra={<Link to="/market">{t("create.changeSpec")}</Link>}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Table<SkuMarketOut>
            size="small"
            rowKey="id"
            dataSource={[sku]}
            columns={columns}
            pagination={false}
          />
          {/* 卡数选择受 available_count 约束:无库存档位禁用 + 提示。CPU 规格不带卡,整行不出 */}
          {!isCpu && (
          <ChipRow
            label={t("market.chipGpuCount")}
            value={gpuCount}
            onChange={setGpuCount}
            options={gpuOptions.map((n) => ({
              value: n,
              label: t("market.cardsUnit", { count: n }),
              disabled: n > (sku.available_count ?? 0),
              disabledReason: t("copy.noStockForGpuCount"),
            }))}
            extra={
              gpuCount > (sku.available_count ?? 0) ? (
                <Typography.Text type="warning" style={{ fontSize: 12 }}>
                  {t("copy.noStockForGpuCount")}
                </Typography.Text>
              ) : undefined
            }
          />
          )}
        </Space>
      </Card>

      {isService ? (
        <>
          <Card title={t("create.containerCard")}>
            <Space orientation="vertical" size={12} style={{ width: "100%" }}>
              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.containerImageLabel")}</Typography.Text>
                <Input
                  placeholder="registry.example.com/your/image:v1.2.0"
                  aria-label={t("create.containerImageLabel")}
                  value={serviceImage}
                  onChange={(e) => setServiceImage(e.target.value)}
                  status={
                    serviceImage.trim() !== "" && !isPinnedImageRef(serviceImage.trim())
                      ? "error"
                      : undefined
                  }
                />
                <Typography.Text type="secondary">{t("copy.serviceImagePinned")}</Typography.Text>
              </Space>

              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.commandLabel")}</Typography.Text>
                <Input
                  placeholder={t("create.commandPlaceholder")}
                  aria-label={t("create.commandLabel")}
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                />
                <Typography.Text type="secondary">{t("create.commandHint")}</Typography.Text>
              </Space>

              <Space orientation="vertical" size={8} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.argsLabel")}</Typography.Text>
                {argRows.map((row, i) => (
                  <Space key={row.id} size={8} style={{ width: "100%" }}>
                    <Input
                      style={{ width: 420 }}
                      placeholder={t("create.argPlaceholder")}
                      aria-label={t("create.argAria", { index: i + 1 })}
                      value={row.value}
                      onChange={(e) =>
                        setArgRows((rows) =>
                          rows.map((r) => (r.id === row.id ? { ...r, value: e.target.value } : r)),
                        )
                      }
                    />
                    <Button onClick={() => setArgRows((rows) => rows.filter((r) => r.id !== row.id))}>
                      {t("create.rowRemove")}
                    </Button>
                  </Space>
                ))}
                <Button
                  onClick={() =>
                    setArgRows((rows) => [...rows, { id: crypto.randomUUID(), value: "" }])
                  }
                >
                  {t("create.addArg")}
                </Button>
              </Space>

              <Space orientation="vertical" size={8} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.envLabel")}</Typography.Text>
                {envRows.map((row, i) => {
                  const err = envError(row);
                  return (
                    <Space key={row.id} orientation="vertical" size={2} style={{ width: "100%" }}>
                      <Space size={8} wrap>
                        <Input
                          style={{ width: 220 }}
                          placeholder={t("create.envNamePlaceholder")}
                          aria-label={t("create.envNameAria", { index: i + 1 })}
                          status={err ? "error" : undefined}
                          value={row.name}
                          onChange={(e) =>
                            setEnvRows((rows) =>
                              rows.map((r) => (r.id === row.id ? { ...r, name: e.target.value } : r)),
                            )
                          }
                        />
                        <Input
                          style={{ width: 300 }}
                          placeholder={t("create.envValuePlaceholder")}
                          aria-label={t("create.envValueAria", { index: i + 1 })}
                          value={row.value}
                          onChange={(e) =>
                            setEnvRows((rows) =>
                              rows.map((r) => (r.id === row.id ? { ...r, value: e.target.value } : r)),
                            )
                          }
                        />
                        <Checkbox
                          checked={row.secret}
                          onChange={(e) =>
                            setEnvRows((rows) =>
                              rows.map((r) => (r.id === row.id ? { ...r, secret: e.target.checked } : r)),
                            )
                          }
                        >
                          {t("create.envSecret")}
                        </Checkbox>
                        <Button onClick={() => setEnvRows((rows) => rows.filter((r) => r.id !== row.id))}>
                          {t("create.rowRemove")}
                        </Button>
                      </Space>
                      {err && (
                        <Typography.Text type="danger" style={{ fontSize: 12 }}>
                          {err}
                        </Typography.Text>
                      )}
                    </Space>
                  );
                })}
                <Button
                  onClick={() =>
                    setEnvRows((rows) => [
                      ...rows,
                      { id: crypto.randomUUID(), name: "", value: "", secret: false },
                    ])
                  }
                >
                  {t("create.addEnv")}
                </Button>
                <Typography.Text type="secondary">{t("create.envSecretHint")}</Typography.Text>
              </Space>
            </Space>
          </Card>

          <Card title={t("create.serviceCard")}>
            <Space orientation="vertical" size={12} style={{ width: "100%" }}>
              <Space size={24} wrap align="start">
                <Space orientation="vertical" size={4}>
                  <Typography.Text type="secondary">{t("create.servicePortLabel")}</Typography.Text>
                  <InputNumber
                    min={1}
                    max={65535}
                    style={{ width: 160 }}
                    placeholder="8000"
                    aria-label={t("create.servicePortLabel")}
                    status={
                      servicePort != null && RESERVED_SERVICE_PORTS.includes(servicePort)
                        ? "error"
                        : undefined
                    }
                    value={servicePort}
                    onChange={(v) => setServicePort(typeof v === "number" ? v : null)}
                  />
                </Space>
                <Space orientation="vertical" size={4}>
                  <Typography.Text type="secondary">{t("create.protocolLabel")}</Typography.Text>
                  {/* TCP / gRPC 未上线:灰置并写明,不隐藏 */}
                  <Radio.Group
                    value="http"
                    options={[
                      { value: "http", label: "HTTP" },
                      { value: "tcp", label: "TCP", disabled: true },
                      { value: "grpc", label: "gRPC", disabled: true },
                    ]}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t("create.protocolSoon")}
                  </Typography.Text>
                </Space>
              </Space>
              <Typography.Text type="secondary">{t("create.servicePortHint")}</Typography.Text>

              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.healthLabel")}</Typography.Text>
                <Input
                  style={{ width: 320 }}
                  placeholder="/healthz"
                  aria-label={t("create.healthLabel")}
                  status={
                    healthPath.trim() !== "" && !healthPath.trim().startsWith("/") ? "error" : undefined
                  }
                  value={healthPath}
                  onChange={(e) => setHealthPath(e.target.value)}
                />
                <Typography.Text type="secondary">{t("create.healthHint")}</Typography.Text>
              </Space>

              <Space orientation="vertical" size={4}>
                <Typography.Text type="secondary">{t("create.endpointLabel")}</Typography.Text>
                <Typography.Text type="secondary">{t("create.endpointPending")}</Typography.Text>
              </Space>

              <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                <Typography.Text type="secondary">{t("create.authLabel")}</Typography.Text>
                <Radio.Group
                  value={requireApiKey ? "key" : "public"}
                  onChange={(e) => setRequireApiKey(e.target.value === "key")}
                  options={[
                    { value: "key", label: t("create.authRequire") },
                    { value: "public", label: t("create.authPublic") },
                  ]}
                />
                {!requireApiKey && (
                  <Typography.Text type="warning">{t("create.authPublicHint")}</Typography.Text>
                )}
                <Typography.Text type="secondary">{t("copy.serviceGatewayAuth")}</Typography.Text>
              </Space>
            </Space>
          </Card>
        </>
      ) : (
      <Card title={t("create.imageCard")}>
        <Tabs
          activeKey={imageTab}
          onChange={(k) => setImageTab(k as "platform" | "custom")}
          items={[
            {
              key: "platform",
              label: t("create.tabPlatform"),
              children: (
                <Space orientation="vertical" style={{ width: "100%" }}>
                  {imagesQ.isError ? (
                    // 镜像清单加载失败绝不伪装成「没有可用镜像」(购买路径硬停)
                    <DataErrorAlert onRetry={() => void imagesQ.refetch()} />
                  ) : (
                    <>
                      <Cascader
                        style={{ width: "100%" }}
                        options={cascade}
                        value={platformImage}
                        onChange={(v) => setPlatformImage(v as string[])}
                        placeholder={t("create.cascadePlaceholder")}
                      />
                      <Typography.Text type="secondary">
                        {/* CPU 规格落无卡机,平台镜像不在那儿预热(全是 CUDA 镜像,铺过去是死重量)——别对它承诺秒级启动 */}
                        {isCpu ? t("create.prewarmedNotForCpu") : t("create.prewarmed")}
                      </Typography.Text>
                    </>
                  )}
                </Space>
              ),
            },
            {
              key: "custom",
              label: t("create.tabCustom"),
              children: (
                <Space orientation="vertical" style={{ width: "100%" }}>
                  <Input
                    placeholder="registry.example.com/your/image:tag"
                    value={customImage}
                    onChange={(e) => setCustomImage(e.target.value)}
                  />
                  <Typography.Text type="secondary">
                    {t("create.customImageHint")}
                  </Typography.Text>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      )}

      <Card title={t("create.diskCard")}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Radio.Group
            value={diskMode}
            onChange={(e) => setDiskMode(e.target.value as typeof diskMode)}
            options={[
              { value: "none", label: t("create.diskNone") },
              { value: "new", label: t("create.diskNew") },
              { value: "existing", label: t("create.diskExisting") },
            ]}
          />
          {diskMode === "new" && (
            <>
              <Space size={12}>
                <Typography.Text type="secondary">{t("storage.nameLabel")}</Typography.Text>
                <Input
                  style={{ width: 260 }}
                  maxLength={64}
                  value={newDiskName}
                  onChange={(e) => setNewDiskName(e.target.value)}
                />
              </Space>
              <Slider
                min={policies?.disk_min_gb}
                max={policies?.disk_max_gb}
                step={10}
                value={newDiskGb}
                onChange={setNewDiskGb}
                disabled={!policies}
              />
              <Typography.Text type="secondary">
                {formatSizeGb(newDiskGb)}
                {diskPriceGbMonth
                  ? ` · ${t("common.gbMonthPrice", { price: diskPriceGbMonth })},${t("common.dailyApprox", { amount: diskDaily })}`
                  : ""}
                ;{t("create.diskAutoCreateNote")}
              </Typography.Text>
            </>
          )}
          {diskMode === "existing" &&
            (disksQ.isError ? (
              // 盘清单加载失败绝不伪装成「没有可挂载的盘」
              <DataErrorAlert onRetry={() => void disksQ.refetch()} />
            ) : (
              <Select
                style={{ width: 320 }}
                placeholder={t("create.selectDiskPlaceholder")}
                value={existingDiskId}
                onChange={setExistingDiskId}
                options={(disks ?? [])
                  .filter((d) => d.status === "active" && d.mounted_instance_id == null)
                  .map((d) => ({
                    value: d.id,
                    label: `${d.name}(${formatSizeGb(d.size_gb)})`,
                  }))}
                notFoundContent={t("create.noMountableDisks")}
              />
            ))}
          <Typography.Text type="secondary">
            {t("create.diskIndependentNote")}
          </Typography.Text>
        </Space>
      </Card>

      {!isService && <Card title={t("create.sshCard")}>{sshKeyPicker}</Card>}

      <Card title={t("create.nameCard")}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Input
            placeholder={t("create.namePlaceholder")}
            maxLength={64}
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ width: 320 }}
          />
          {isService && (
            <>
              <Checkbox checked={withSsh} onChange={(e) => setWithSsh(e.target.checked)}>
                {t("create.withSsh")}
              </Checkbox>
              <Typography.Text type="secondary">{t("create.withSshHint")}</Typography.Text>
              {/* 勾了才要公钥:后端对 with_ssh 的实例同样要求 ssh_key_ids 非空 */}
              {withSsh && sshKeyPicker}
            </>
          )}
        </Space>
      </Card>

      <CheckoutBar
        summary={
          isCpu
            ? t("create.summaryCpu", { vcpu: sku.vcpu, mem: sku.mem_gb })
            : t("create.summary", {
                model: sku.gpu_model,
                count: gpuCount,
                vcpu: sku.vcpu * gpuCount,
                mem: sku.mem_gb * gpuCount,
              })
        }
        items={[
          {
            label: t("create.dailyCostLabel"),
            hint: t("create.dailyCostHint"),
            value: t("common.dailyApprox", { amount: diskGb > 0 && diskPriceGbMonth ? diskDaily : "0.00" }),
          },
          { label: t("create.configCostLabel"), value: formatHourlyPrice(hourlyTotal) },
        ]}
        detail={
          <Space orientation="vertical" size={4} style={{ maxWidth: 360 }}>
            <span>
              {isCpu
                ? t("create.detailInstanceLineCpu", { total: formatHourlyPrice(hourlyTotal) })
                : t("create.detailInstanceLine", {
                    unit: formatHourlyPrice(sku.price_hourly),
                    count: gpuCount,
                    total: formatHourlyPrice(hourlyTotal),
                  })}
            </span>
            <span>
              {diskGb > 0 && diskPriceGbMonth
                ? t("create.detailDiskLine", { size: diskGb, price: t("common.gbMonthPrice", { price: diskPriceGbMonth }) })
                : t("create.detailDiskNone")}
            </span>
            <Typography.Text type="secondary">
              {t("create.balanceNeedNote")}
            </Typography.Text>
          </Space>
        }
        balance={wallet?.balance ?? null}
        balanceReady={balanceReady}
        actions={
          <>
            <Button size="large" onClick={() => void navigate({ to: "/market" })}>
              {t("create.cancel")}
            </Button>
            {!balanceReady ? (
              // 余额未就绪:主 CTA 保持 primary + loading,不出现红色文案
              <Button type="primary" size="large" loading disabled>
                {isService ? t("create.deployService") : t("create.createAndStart")}
              </Button>
            ) : enough ? (
              <Tooltip
                title={
                  canSubmit ? undefined : (serviceIssue ?? t("create.selectImageAndKey"))
                }
              >
                <Button
                  type="primary"
                  size="large"
                  disabled={!canSubmit}
                  loading={submitting || create.isPending}
                  onClick={submit}
                >
                  {isService ? t("create.deployService") : t("create.createAndStart")}
                </Button>
              </Tooltip>
            ) : (
              <Link to="/billing">
                <Button type="primary" danger size="large">
                  {t("create.notEnoughGoRecharge")}
                </Button>
              </Link>
            )}
          </>
        }
      />

      <Modal
        title={t("create.ecoModalTitle")}
        open={ecoOpen}
        onCancel={() => {
          setEcoOpen(false);
          setEcoChecked(false);
        }}
        footer={
          <Space>
            <Button
              onClick={() => {
                setEcoOpen(false);
                setEcoChecked(false);
              }}
            >
              {t("create.cancel")}
            </Button>
            <Button
              type="primary"
              disabled={!ecoChecked}
              loading={submitting || create.isPending}
              onClick={() => {
                setEcoOpen(false);
                void doCreate();
              }}
            >
              {t("create.ecoConfirm")}
            </Button>
          </Space>
        }
      >
        <ul style={{ paddingLeft: 20 }}>
          {[t("copy.ecoTierConsent.c1"), t("copy.ecoTierConsent.c2"), t("copy.ecoTierConsent.c3"), t("copy.ecoTierConsent.c4")].map((line) => (
            <li key={line} style={{ marginBottom: 8 }}>
              {line}
            </li>
          ))}
        </ul>
        <Checkbox checked={ecoChecked} onChange={(e) => setEcoChecked(e.target.checked)}>
          {t("create.ecoAgree")}
        </Checkbox>
      </Modal>
    </div>
  );
}
