"""GPU 型号归一化:SKU 手填 / nvidia-smi / lspci / GFD label / fake 注入 → canonical 短名,
未识别返回 None。

规则:RTX 消费卡 = RTX<数字><后缀>;多容量家族(A100/A800/H100/H800/H200/V100)带 -{显存}G;
单容量卡(T4/L4/L40S/A10/GB10 等)取家族名;CMP 矿卡取 CMP<数字>HX。
巡检写入节点 label `superdl.io/gpu-model`。
"""

import re

# 多容量家族:canonical 追加 -{n}G;无显存信息退家族名(见 model_matches)
_MULTI_VRAM_FAMILIES = frozenset({"A100", "A800", "H100", "H800", "H200", "V100"})

# 单容量/免后缀白名单(数据中心与推理卡)
_PLAIN_FAMILIES = frozenset(
    {"T4", "L4", "L20", "L40", "L40S", "A10", "A16", "A30", "A40", "H20", "B200", "GB200", "GB10"}
)

# 噪声 token(厂牌/系列词)与形态 token(封装/显存介质)
_NOISE = frozenset({"NVIDIA", "CORPORATION", "GEFORCE", "TESLA", "QUADRO", "GRAPHICS", "DEVICE"})
_FORM = frozenset({"SXM", "SXM2", "SXM4", "SXM5", "PCIE", "NVL", "HBM2", "HBM2E", "HBM3", "OEM"})

# 常见卡型默认单卡显存(GB),bootstrap 未上报时兜底;未知=0
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
    "CMP170HX": 8,
    # 统一内存(nvidia-smi 显存 N/A):按 HAMi preConfiguredDeviceMemory 口径
    "GB10": 96,
}

_RTX_RE = re.compile(r"\bRTX\s*(\d{3,4})\s*(TI|SUPER|D)?\b")
_CMP_RE = re.compile(r"\bCMP\s*(\d{2,3})\s*HX\b")
_FAMILY_RE = re.compile(r"\b([AHLBV]\d{2,4}S?|GB\d{2,3}|T4)\b")
_VRAM_RE = re.compile(r"\b(\d{2,3})\s*GB?\b")


def canonical_gpu_model(raw: str | None) -> str | None:
    """归一化到 canonical;未识别返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    # lspci 剥壳:取方括号内容
    m = re.search(r"\[([^\]]+)\]", text)
    if m:
        text = m.group(1)
    # 统一分隔与大小写
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
    m = _CMP_RE.search(flat)
    if m:
        return f"CMP{m.group(1)}HX"

    m = _FAMILY_RE.search(flat)
    if m:
        family = m.group(1)
        if family in _MULTI_VRAM_FAMILIES:
            return f"{family}-{vram}G" if vram else family
        if family in _PLAIN_FAMILIES:
            return family
    return None


# 支持 MIG 硬件切分的家族(canonical 取 "-" 前一段比对):数据中心 Ampere 及以后的大核。
# L4/L20/L40/L40S、RTX、CMP、GB10 均不支持;切到 mig 池的机型闸按此判。
MIG_CAPABLE_FAMILIES = frozenset(
    {"A100", "A800", "A30", "H100", "H800", "H200", "H20", "B200", "GB200"}
)


def supports_mig(canonical: str | None) -> bool:
    """canonical 型号是否支持 MIG 切分;未识别(None)一律 False(fail-closed)。"""
    if not canonical:
        return False
    return canonical.split("-")[0] in MIG_CAPABLE_FAMILIES


def model_matches(sku_model: str | None, node_model: str | None) -> bool:
    """canonical 匹配:相等,或 SKU 只写家族而节点带显存后缀(A100 匹配 A100-80G);反向不成立。"""
    if not sku_model or not node_model:
        return False
    if sku_model == node_model:
        return True
    return node_model.startswith(f"{sku_model}-")


def default_vram_gb(canonical: str | None) -> int:
    return DEFAULT_VRAM_GB.get(canonical or "", 0)
