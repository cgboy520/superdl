"""GPU 型号归一化:五种来源格式 → canonical 短名(调度 label 与 SKU 匹配的单一口径)。

来源格式:SKU 手填(RTX4090)/ nvidia-smi(NVIDIA GeForce RTX 4090)/ lspci 尾段
(NVIDIA Corporation AD102 [GeForce RTX 4090] (rev a1))/ GFD label
(NVIDIA-GeForce-RTX-4090)/ fake 注入。未识别返回 None(台账保留 raw,SKU 下拉不出现)。

canonical 规则:RTX 消费卡 = RTX<数字><后缀>(4090 仅 24G,不带显存);
数据中心同名多容量家族(A100/A800/H100/H800/H200/V100)带 -{显存}G 后缀;
T4/L4/L40S/A10 等单容量卡取家族名。巡检将 canonical 写入节点 label
`superdl.io/gpu-model`,gpu_adapter 以 nodeSelector 依赖它(WP26)。
"""

import re

# 同名多容量家族:canonical 追加 -{n}G;无显存信息时退家族名(匹配语义见 model_matches)
_MULTI_VRAM_FAMILIES = frozenset({"A100", "A800", "H100", "H800", "H200", "V100"})

# 单容量/免后缀白名单(数据中心与推理卡)
_PLAIN_FAMILIES = frozenset(
    {"T4", "L4", "L20", "L40", "L40S", "A10", "A16", "A30", "A40", "H20", "B200", "GB200"}
)

# 噪声 token(厂牌/系列词)与形态 token(封装/显存介质)
_NOISE = frozenset({"NVIDIA", "CORPORATION", "GEFORCE", "TESLA", "QUADRO", "GRAPHICS", "DEVICE"})
_FORM = frozenset({"SXM", "SXM2", "SXM4", "SXM5", "PCIE", "NVL", "HBM2", "HBM2E", "HBM3", "OEM"})

# 全串兜底表:实机遇到规则覆盖不了的原始串时在此登记(优先级最高)
OVERRIDES: dict[str, str] = {}

# 常见卡型默认单卡显存(GB):bootstrap 未上报 memory.total 时的兜底,未知=0
DEFAULT_VRAM_GB: dict[str, int] = {
    "RTX3090": 24,
    "RTX4090": 24,
    "RTX4090D": 24,
    "RTX5090": 32,
    "A10": 24,
    "A100-40G": 40,
    "A100-80G": 80,
    "A800-80G": 80,
    "H100-80G": 80,
    "H20": 96,
    "H200-141G": 141,
    "L4": 24,
    "L20": 48,
    "L40": 48,
    "L40S": 48,
    "T4": 16,
    "V100-16G": 16,
    "V100-32G": 32,
}

_RTX_RE = re.compile(r"\bRTX\s*(\d{3,4})\s*(TI|SUPER|D)?\b")
_FAMILY_RE = re.compile(r"\b([AHLBV]\d{2,4}S?|GB\d{3}|T4)\b")
_VRAM_RE = re.compile(r"\b(\d{2,3})\s*GB?\b")


def canonical_gpu_model(raw: str | None) -> str | None:
    """归一化到 canonical;未识别返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    if text in OVERRIDES:
        return OVERRIDES[text]
    # lspci 剥壳:取方括号内容
    m = re.search(r"\[([^\]]+)\]", text)
    if m:
        text = m.group(1)
    # 统一分隔与大小写(吃掉 GFD 的连字符)
    text = re.sub(r"[-_]", " ", text).upper()
    text = re.sub(r"\(.*?\)", " ", text)  # (rev a1) 之类
    tokens = [t for t in text.split() if t and t not in _NOISE]
    vram: int | None = None
    kept: list[str] = []
    for t in tokens:
        if t in _FORM:
            continue
        mv = _VRAM_RE.fullmatch(t)
        if mv:
            vram = int(mv.group(1))
            continue
        kept.append(t)
    flat = " ".join(kept)

    m = _RTX_RE.search(flat)
    if m:
        return f"RTX{m.group(1)}{m.group(2) or ''}"

    m = _FAMILY_RE.search(flat)
    if m:
        family = m.group(1)
        if family in _MULTI_VRAM_FAMILIES:
            return f"{family}-{vram}G" if vram else family
        if family in _PLAIN_FAMILIES:
            return family
    return None


def model_matches(sku_model: str | None, node_model: str | None) -> bool:
    """SKU 与节点 canonical 匹配:相等,或节点带显存后缀而 SKU 只写家族(A100 匹配 A100-80G)。

    反向不成立:SKU 指明 A100-80G 时不匹配裸 A100 节点(显存不确定不许卖)。
    """
    if not sku_model or not node_model:
        return False
    if sku_model == node_model:
        return True
    return node_model.startswith(f"{sku_model}-")


def default_vram_gb(canonical: str | None) -> int:
    return DEFAULT_VRAM_GB.get(canonical or "", 0)
