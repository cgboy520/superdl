/**
 * 创建实例:全页,左表单(规格/镜像/数据盘/SSH 密钥/名称)右 sticky 价格栏。
 * 铁律 #3 价格公式摊开+「日常费用」单独一栏;铁律 #5 经济档知情同意。
 */

import { type InstanceOut } from "@superdl/api-client";
import { copy, formatMoney, formatSizeGb, tabularNums } from "@superdl/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  Alert,
  App,
  Button,
  Card,
  Cascader,
  Checkbox,
  Col,
  Divider,
  Input,
  Modal,
  Radio,
  Row,
  Select,
  Slider,
  Space,
  Tabs,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { useCreateInstance } from "../api/mutations";
import { useDisks, useImages, useSkus, useSshKeys, useWallet } from "../api/queries";
import { TierTag } from "../components/common";
import { requireAuth } from "../lib/guard";

export const Route = createFileRoute("/_console/market/create/$skuId")({
  beforeLoad: requireAuth,
  component: CreatePage,
});

const DISK_PRICE_GB_MONTH = 0.035; // 展示用近似值,后端以创建时快照为准

function CreatePage() {
  const { skuId } = Route.useParams();
  const navigate = useNavigate();
  const { message } = App.useApp();

  const { data: skus } = useSkus({});
  const sku = (skus ?? []).find((s) => s.id === Number(skuId));

  const { data: images } = useImages();
  const { data: keys } = useSshKeys();
  const { data: disks } = useDisks();
  const { data: wallet } = useWallet();

  const [gpuCount, setGpuCount] = useState(1);
  const [imageTab, setImageTab] = useState<"platform" | "custom">("platform");
  const [platformImage, setPlatformImage] = useState<string[]>();
  const [customImage, setCustomImage] = useState("");
  const [diskMode, setDiskMode] = useState<"none" | "new" | "existing">("none");
  const [newDiskGb, setNewDiskGb] = useState(100);
  const [existingDiskId, setExistingDiskId] = useState<number>();
  const [keyIds, setKeyIds] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [ecoOpen, setEcoOpen] = useState(false);
  const [ecoChecked, setEcoChecked] = useState(false);
  const [idempotencyKey] = useState(() => crypto.randomUUID());

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

  if (!sku) {
    return <Alert type="warning" showIcon message="规格不存在或已下架" />;
  }

  const hourly = parseFloat(sku.price_hourly) * gpuCount;
  const diskGb = diskMode === "new" ? newDiskGb : 0;
  const diskDaily = (diskGb * DISK_PRICE_GB_MONTH) / 30;
  const balance = parseFloat(wallet?.balance ?? "0");
  const enough = balance >= hourly;

  const imageRef = imageTab === "platform" ? platformImage?.[3] : customImage.trim();
  const canSubmit = Boolean(imageRef) && keyIds.length > 0;

  const doCreate = () => {
    create.mutate({
      body: {
        sku_id: sku.id,
        gpu_count: gpuCount,
        image_ref: imageRef ?? "",
        ssh_key_ids: keyIds,
        name: name || null,
        data_disk_id: diskMode === "existing" ? existingDiskId : null,
      },
      idempotencyKey,
    });
  };

  const submit = () => {
    if (sku.tier === "shared_eco") {
      setEcoOpen(true);
      return;
    }
    doCreate();
  };

  const gpuOptions = Array.from({ length: sku.max_gpus_per_instance }, (_, i) => i + 1).filter(
    (n) => [1, 2, 4, 8].includes(n) || n === sku.max_gpus_per_instance,
  );

  return (
    <Row gutter={24}>
      <Col span={16}>
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Card
            title="已选规格"
            extra={<Link to="/market">更换规格</Link>}
          >
            <Space direction="vertical" size={8}>
              <Space>
                <Typography.Text strong>{sku.name}</Typography.Text>
                <TierTag tier={sku.tier} />
              </Space>
              <Typography.Text type="secondary">
                {sku.gpu_model} · {sku.vram_gb}G 显存 · {sku.vcpu} vCPU · {sku.mem_gb}G 内存 ·
                实例盘 {sku.disk_gb}G
              </Typography.Text>
              <div>
                <Typography.Text style={{ marginRight: 12 }}>GPU 数量</Typography.Text>
                <Radio.Group
                  optionType="button"
                  value={gpuCount}
                  onChange={(e) => setGpuCount(e.target.value as number)}
                  options={gpuOptions.map((n) => ({ value: n, label: `${n} 卡` }))}
                />
              </div>
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
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Cascader
                        style={{ width: "100%" }}
                        options={cascade}
                        value={platformImage}
                        onChange={(v) => setPlatformImage(v as string[])}
                        placeholder="框架 / 版本 / Python / CUDA"
                      />
                      <Typography.Text type="secondary">
                        平台镜像已在节点预热,秒级启动
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  key: "custom",
                  label: "自定义镜像",
                  children: (
                    <Space direction="vertical" style={{ width: "100%" }}>
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
              ]}
            />
          </Card>

          <Card title="数据盘(可选)">
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
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
                  <Slider
                    min={10}
                    max={1024}
                    step={10}
                    value={newDiskGb}
                    onChange={setNewDiskGb}
                  />
                  <Typography.Text type="secondary">
                    {formatSizeGb(newDiskGb)} · 约 ¥{diskDaily.toFixed(2)}/日;将先在「存储」页创建后再挂载,本次下单不自动创建
                  </Typography.Text>
                  <Alert
                    type="info"
                    showIcon
                    message="提示:请先到「存储」页创建数据盘,再回来选择「挂载已有盘」"
                  />
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
              <Alert
                type="warning"
                showIcon
                message={copy.sshKeyOnly}
                description={
                  <Link to="/settings">
                    <Button size="small" type="primary">
                      去添加 SSH 公钥
                    </Button>
                  </Link>
                }
              />
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
        </Space>
      </Col>

      <Col span={8}>
        <div style={{ position: "sticky", top: 24 }}>
          <Card title="费用明细">
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Typography.Text type="secondary">开机费用(按量,关机即停)</Typography.Text>
              <Row justify="space-between">
                <span>
                  实例 {formatMoney(sku.price_hourly)}/时 × {gpuCount} 卡
                </span>
                <span style={tabularNums}>¥{hourly.toFixed(2)}/时</span>
              </Row>
              <Divider style={{ margin: "8px 0" }} />
              <Typography.Text type="secondary">日常费用(关机也会产生)</Typography.Text>
              <Row justify="space-between">
                <span>数据盘</span>
                <span style={tabularNums}>
                  {diskMode === "existing" || diskMode === "new"
                    ? `约 ¥${diskDaily > 0 ? diskDaily.toFixed(2) : "按已有盘计"}/日`
                    : "¥0.00/日"}
                </span>
              </Row>
              <Divider style={{ margin: "8px 0" }} />
              <Row justify="space-between">
                <span>当前余额</span>
                <span style={tabularNums}>{formatMoney(wallet?.balance)}</span>
              </Row>
              {enough ? (
                <Button
                  type="primary"
                  block
                  size="large"
                  disabled={!canSubmit}
                  loading={create.isPending}
                  onClick={submit}
                  title={
                    canSubmit ? undefined : "请先选择镜像与至少一个 SSH 公钥"
                  }
                >
                  创建并开机
                </Button>
              ) : (
                <Link to="/billing">
                  <Button type="primary" danger block size="large">
                    余额不足,去充值
                  </Button>
                </Link>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                开机前需余额 ≥ 1 小时预估费用;计费依据为实例事件流水,精确到秒
              </Typography.Text>
            </Space>
          </Card>
        </div>
      </Col>

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
            loading={create.isPending}
            onClick={() => {
              setEcoOpen(false);
              doCreate();
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
    </Row>
  );
}
