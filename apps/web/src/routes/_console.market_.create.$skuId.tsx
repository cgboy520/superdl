/**
 * 创建实例:AutoDL 式单栏卡片流(计费方式/已选规格/镜像/数据盘/SSH/名称)+ 底部结算条。
 * 铁律 #3 「日常费用(关机也产生)」与「配置费用」分栏摊开;铁律 #5 经济档知情同意。
 * 数据盘「新建」为行内直建:提交时先建盘再建实例;建盘成功而实例失败须提示盘已计费。
 */

import { type DiskOut, type InstanceOut, type SkuMarketOut } from "@superdl/api-client";
import { copy, formatHourlyPrice, formatSizeGb, mulPrice } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Cascader,
  Checkbox,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Slider,
  Space,
  Table,
  Tabs,
  Tooltip,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { useAddSshKey, useCreateDisk, useCreateInstance } from "../api/mutations";
import { useDisks, useImages, usePolicies, useSkus, useSshKeys, useWallet } from "../api/queries";
import { ChipRow } from "../components/ChipRow";
import { CheckoutBar } from "../components/CheckoutBar";
import { TierTag } from "../components/common";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/market_/create/$skuId")({
  validateSearch: (search: Record<string, unknown>): { gpus?: number } => {
    // 市场页带入的 GPU 数量(可改)
    const g = Number(search.gpus);
    return Number.isInteger(g) && g >= 1 && g <= 8 ? { gpus: g } : {};
  },
  beforeLoad: requireAuth,
  component: CreatePage,
});

function defaultDiskName(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `data-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function CreatePage() {
  const { skuId } = Route.useParams();
  const { gpus: gpusFromMarket } = Route.useSearch();
  const navigate = useNavigate();
  const { message } = App.useApp();

  const { data: skus } = useSkus({});
  const sku = (skus ?? []).find((s) => s.id === Number(skuId));

  const { data: images } = useImages();
  const { data: keys } = useSshKeys();
  const { data: disks } = useDisks();
  const { data: wallet } = useWallet();
  const { data: policies } = usePolicies();

  const [gpuCount, setGpuCount] = useState(gpusFromMarket ?? 1);
  const [imageTab, setImageTab] = useState<"platform" | "custom">("platform");
  const [platformImage, setPlatformImage] = useState<string[]>();
  const [customImage, setCustomImage] = useState("");
  const [diskMode, setDiskMode] = useState<"none" | "new" | "existing">("none");
  const [newDiskName, setNewDiskName] = useState(defaultDiskName);
  const [newDiskGb, setNewDiskGb] = useState(100);
  const [existingDiskId, setExistingDiskId] = useState<number>();
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [ecoOpen, setEcoOpen] = useState(false);
  const [ecoChecked, setEcoChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [keyForm] = Form.useForm<{ name: string; public_key: string }>();

  const cascade = useMemo(() => {
    const tree: Record<string, Record<string, Record<string, Record<string, string>>>> = {};
    for (const img of images ?? []) {
      ((((tree[img.framework] ??= {})[img.framework_version] ??= {})[img.python_version] ??= {})[
        `CUDA ${img.cuda_version}`
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

  const create = useCreateInstance({
    onSuccess: (data) => {
      const inst = data as InstanceOut;
      message.success(`实例 ${inst.name} 创建中`);
      void navigate({ to: "/instances" });
    },
  });
  const createDisk = useCreateDisk();
  const addKey = useAddSshKey({
    onSuccess: () => {
      message.success("公钥已添加");
      keyForm.resetFields();
    },
  });

  if (!sku) {
    return <Alert type="warning" showIcon title="规格不存在或已下架" />;
  }

  const diskPriceGbMonth = policies?.disk_price_gb_month;
  const diskGb =
    diskMode === "new"
      ? newDiskGb
      : diskMode === "existing"
        ? ((disks ?? []).find((d) => d.id === existingDiskId)?.size_gb ?? 0)
        : 0;
  // 「约 ¥X/日」为展示层估算(月价/30);入账以后端日结为准
  const diskDaily = diskPriceGbMonth ? (diskGb * Number(diskPriceGbMonth)) / 30 : 0;
  const hourlyTotal = mulPrice(sku.price_hourly, gpuCount);
  const enough = Number(wallet?.balance ?? "0") >= Number(hourlyTotal);

  const imageRef = imageTab === "platform" ? platformImage?.[3] : customImage.trim();
  const canSubmit = Boolean(imageRef) && keyIds.length > 0;

  const doCreate = async () => {
    setSubmitting(true);
    try {
      let diskId: number | null = diskMode === "existing" ? (existingDiskId ?? null) : null;
      if (diskMode === "new") {
        let disk: DiskOut;
        try {
          disk = (await createDisk.mutateAsync({
            name: newDiskName.trim() || defaultDiskName(),
            size_gb: newDiskGb,
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
            gpu_count: gpuCount,
            image_ref: imageRef ?? "",
            ssh_key_ids: keyIds,
            name: name || null,
            data_disk_id: diskId,
          },
          idempotencyKey,
        });
      } catch {
        if (diskMode === "new" && diskId != null) {
          message.warning(copy.diskCreatedButInstanceFailed, 6);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  const submit = () => {
    if (sku.tier === "shared_eco") {
      setEcoOpen(true);
      return;
    }
    void doCreate();
  };

  const gpuOptions = Array.from({ length: sku.max_gpus_per_instance }, (_, i) => i + 1).filter(
    (n) => [1, 2, 4, 8].includes(n) || n === sku.max_gpus_per_instance,
  );

  const skuColumns = [
    {
      title: "规格",
      render: (_: unknown, s: SkuMarketOut) => (
        <Space>
          <Typography.Text strong>{s.name}</Typography.Text>
          <TierTag tier={s.tier} />
        </Space>
      ),
    },
    {
      title: "GPU / 显存",
      render: (_: unknown, s: SkuMarketOut) =>
        s.tier.startsWith("shared")
          ? `${s.gpu_model} · ${s.vram_gb}G · ${s.gpu_cores_pct}% 算力(均值)`
          : s.tier === "mig"
            ? `${s.gpu_model} · ${s.vram_gb}G · MIG ${s.mig_profile ?? "切分"}`
            : `${s.gpu_model} · ${s.vram_gb}G · 整卡`,
    },
    {
      title: "实例配置",
      render: (_: unknown, s: SkuMarketOut) => `${s.vcpu} vCPU / ${s.mem_gb}G 内存`,
    },
    { title: "实例盘", render: (_: unknown, s: SkuMarketOut) => `${s.disk_gb}G(含 100G)` },
    { title: "最高 CUDA", render: (_: unknown, s: SkuMarketOut) => s.cuda_max ?? "-" },
    {
      title: "价格(单卡)",
      render: (_: unknown, s: SkuMarketOut) => (
        <span style={{ fontWeight: 700 }}>{formatHourlyPrice(s.price_hourly)}</span>
      ),
    },
  ];

  return (
    <Space orientation="vertical" size={16} style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        创建实例
      </Typography.Title>

      <Card title="计费方式" styles={{ body: { paddingBlock: 16 } }}>
        <ChipRow
          label="计费方式"
          value="hourly"
          onChange={() => undefined}
          options={[
            { value: "hourly", label: "按量计费" },
            { value: "daily", label: "包日", disabled: true, disabledReason: copy.billingModeComingSoon },
            { value: "weekly", label: "包周", disabled: true, disabledReason: copy.billingModeComingSoon },
            { value: "monthly", label: "包月", disabled: true, disabledReason: copy.billingModeComingSoon },
          ]}
        />
      </Card>

      <Card title="已选规格" extra={<Link to="/market">更换规格</Link>}>
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Table<SkuMarketOut>
            size="small"
            rowKey="id"
            dataSource={[sku]}
            columns={skuColumns}
            pagination={false}
          />
          <ChipRow
            label="GPU 数量"
            value={gpuCount}
            onChange={setGpuCount}
            options={gpuOptions.map((n) => ({ value: n, label: `${n} 卡` }))}
          />
        </Space>
      </Card>

      <Card title="镜像">
        <Tabs
          activeKey={imageTab}
          onChange={(k) => setImageTab(k as "platform" | "custom")}
          items={[
            {
              key: "platform",
              label: "平台镜像",
              children: (
                <Space orientation="vertical" style={{ width: "100%" }}>
                  <Cascader
                    style={{ width: "100%" }}
                    options={cascade}
                    value={platformImage}
                    onChange={(v) => setPlatformImage(v as string[])}
                    placeholder="框架 / 版本 / Python / CUDA"
                  />
                  <Typography.Text type="secondary">平台镜像已在节点预热,秒级启动</Typography.Text>
                </Space>
              ),
            },
            {
              key: "custom",
              label: "自定义镜像",
              children: (
                <Space orientation="vertical" style={{ width: "100%" }}>
                  <Input
                    placeholder="registry.example.com/your/image:tag"
                    value={customImage}
                    onChange={(e) => setCustomImage(e.target.value)}
                  />
                  <Typography.Text type="secondary">
                    镜像需内置 SSH(22)与 JupyterLab(8888);私有仓库拉取凭据请联系客服配置
                  </Typography.Text>
                </Space>
              ),
            },
            {
              key: "mine",
              label: <Tooltip title={copy.myImagesComingSoon}>我的镜像</Tooltip>,
              disabled: true,
              children: null,
            },
          ]}
        />
      </Card>

      <Card title="数据盘(可选)">
        <Space orientation="vertical" size={12} style={{ width: "100%" }}>
          <Radio.Group
            value={diskMode}
            onChange={(e) => setDiskMode(e.target.value as typeof diskMode)}
            options={[
              { value: "none", label: "不需要" },
              { value: "new", label: "新建数据盘" },
              { value: "existing", label: "挂载已有盘" },
            ]}
          />
          {diskMode === "new" && (
            <>
              <Space size={12}>
                <Typography.Text type="secondary">名称</Typography.Text>
                <Input
                  style={{ width: 260 }}
                  maxLength={64}
                  value={newDiskName}
                  onChange={(e) => setNewDiskName(e.target.value)}
                />
              </Space>
              <Slider
                min={policies?.disk_min_gb ?? 10}
                max={policies?.disk_max_gb ?? 1024}
                step={10}
                value={newDiskGb}
                onChange={setNewDiskGb}
              />
              <Typography.Text type="secondary">
                {formatSizeGb(newDiskGb)}
                {diskPriceGbMonth
                  ? ` · ¥${diskPriceGbMonth}/GB·月,约 ¥${diskDaily.toFixed(2)}/日`
                  : ""}
                ;提交时将自动创建并随实例挂载
              </Typography.Text>
            </>
          )}
          {diskMode === "existing" && (
            <Select
              style={{ width: 320 }}
              placeholder="选择数据盘"
              value={existingDiskId}
              onChange={setExistingDiskId}
              options={(disks ?? [])
                .filter((d) => d.status === "active" && d.mounted_instance_id == null)
                .map((d) => ({
                  value: d.id,
                  label: `${d.name}(${formatSizeGb(d.size_gb)})`,
                }))}
              notFoundContent="暂无可挂载的数据盘"
            />
          )}
          <Typography.Text type="secondary">
            数据盘独立于实例:关机与释放均保留;{copy.dailyCostNote}
          </Typography.Text>
        </Space>
      </Card>

      <Card title="SSH 密钥">
        {(keys ?? []).length === 0 ? (
          <Space orientation="vertical" size={12} style={{ width: "100%" }}>
            <Alert type="warning" showIcon title={copy.sshKeyOnly} />
            <Form
              form={keyForm}
              layout="inline"
              onFinish={(v) => addKey.mutate({ name: v.name, public_key: v.public_key })}
            >
              <Form.Item name="name" rules={[{ required: true, message: "名称必填" }]}>
                <Input placeholder="密钥名称" style={{ width: 160 }} />
              </Form.Item>
              <Form.Item
                name="public_key"
                rules={[{ required: true, message: "公钥内容必填" }]}
                style={{ flex: 1 }}
              >
                <Input placeholder="ssh-ed25519 AAAA… 或 ssh-rsa AAAA…" />
              </Form.Item>
              <Form.Item>
                <Button type="primary" htmlType="submit" loading={addKey.isPending}>
                  添加公钥
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
        )}
      </Card>

      <Card title="实例名称(可选)">
        <Input
          placeholder="不填则自动生成"
          maxLength={64}
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ width: 320 }}
        />
      </Card>

      <CheckoutBar
        summary={`${sku.gpu_model} × ${gpuCount} · ${sku.vcpu * gpuCount} vCPU · ${sku.mem_gb * gpuCount}G 内存`}
        items={[
          {
            label: "日常费用",
            hint: "关机也会产生",
            value:
              diskGb > 0 && diskPriceGbMonth ? `约 ¥${diskDaily.toFixed(2)}/日` : "¥0.00/日",
          },
          { label: "配置费用", value: formatHourlyPrice(hourlyTotal) },
        ]}
        detail={
          <Space orientation="vertical" size={4} style={{ maxWidth: 360 }}>
            <span>
              实例:{formatHourlyPrice(sku.price_hourly)} × {gpuCount} 卡 ={" "}
              {formatHourlyPrice(hourlyTotal)}
            </span>
            <span>
              数据盘:
              {diskGb > 0 && diskPriceGbMonth
                ? `${diskGb}G × ¥${diskPriceGbMonth}/GB·月(按日折算,关机也计费)`
                : "无"}
            </span>
            <Typography.Text type="secondary">
              开机前需余额 ≥ 1 小时预估费用;{copy.eventsAreBilling}
            </Typography.Text>
          </Space>
        }
        balance={wallet?.balance ?? null}
        actions={
          <>
            <Button size="large" onClick={() => void navigate({ to: "/market" })}>
              取消
            </Button>
            {enough ? (
              <Tooltip title={canSubmit ? undefined : "请先选择镜像与至少一个 SSH 公钥"}>
                <Button
                  type="primary"
                  size="large"
                  disabled={!canSubmit}
                  loading={submitting || create.isPending}
                  onClick={submit}
                >
                  创建并开机
                </Button>
              </Tooltip>
            ) : (
              <Link to="/billing">
                <Button type="primary" danger size="large">
                  余额不足,去充值
                </Button>
              </Link>
            )}
          </>
        }
      />

      <Modal
        title="共享·经济档服务说明"
        open={ecoOpen}
        onCancel={() => {
          setEcoOpen(false);
          setEcoChecked(false);
        }}
        footer={
          <Button
            type="primary"
            disabled={!ecoChecked}
            loading={submitting || create.isPending}
            onClick={() => {
              setEcoOpen(false);
              void doCreate();
            }}
          >
            我已知悉,继续创建
          </Button>
        }
      >
        <ul style={{ paddingLeft: 20 }}>
          {copy.ecoTierConsent.map((line) => (
            <li key={line} style={{ marginBottom: 8 }}>
              {line}
            </li>
          ))}
        </ul>
        <Checkbox checked={ecoChecked} onChange={(e) => setEcoChecked(e.target.checked)}>
          我已阅读并同意上述服务说明
        </Checkbox>
      </Modal>
    </Space>
  );
}
