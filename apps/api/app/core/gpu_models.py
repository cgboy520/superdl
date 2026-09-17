"""GPU model normalisation: SKU input / nvidia-smi / lspci / GFD label / fake injection → canonical
short name, None when unrecognised.

Rules: RTX consumer cards = RTX<digits><suffix>; multi-capacity families append -{vram}G when the
VRAM is known, otherwise the family name only; single-capacity cards (T4/L4/L40S/A10/GB10 ...) take
the family name; CMP mining cards become CMP<digits>HX.
"""

import re

_MULTI_VRAM_FAMILIES = frozenset({"A100", "A800", "H100", "H800", "H200", "V100"})

_PLAIN_FAMILIES = frozenset(
    {"T4", "L4", "L20", "L40", "L40S", "A10", "A16", "A30", "A40", "H20", "B200", "GB200", "GB10"}
)

_NOISE = frozenset({"NVIDIA", "CORPORATION", "GEFORCE", "TESLA", "QUADRO", "GRAPHICS", "DEVICE"})
_FORM = frozenset({"SXM", "SXM2", "SXM4", "SXM5", "PCIE", "NVL", "HBM2", "HBM2E", "HBM3", "OEM"})

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
    "GB10": 96,
}

_RTX_RE = re.compile(r"\bRTX\s*(\d{3,4})\s*(TI|SUPER|D)?\b")
_CMP_RE = re.compile(r"\bCMP\s*(\d{2,3})\s*HX\b")
_FAMILY_RE = re.compile(r"\b([AHLBV]\d{2,4}S?|GB\d{2,3}|T4)\b")
_VRAM_RE = re.compile(r"\b(\d{2,3})\s*GB?\b")


def canonical_gpu_model(raw: str | None) -> str | None:
    """Normalise to canonical; None when unrecognised."""
    if not raw:
        return None
    text = raw.strip()
    m = re.search(r"\[([^\]]+)\]", text)
    if m:
        text = m.group(1)
    text = re.sub(r"[-_]", " ", text).upper()
    text = re.sub(r"\(.*?\)", " ", text)
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


# Aligned with the Supported GPUs list of the NVIDIA MIG User Guide; A800 / H800 are the China
# variants of A100 / H100 and support MIG as well.
MIG_CAPABLE_FAMILIES = frozenset(
    {"A100", "A800", "A30", "H100", "H800", "H200", "H20", "B200", "GB200"}
)


def supports_mig(canonical: str | None) -> bool:
    """Whether the canonical model supports MIG partitioning; unrecognised (None) is always False
    (fail-closed)."""
    if not canonical:
        return False
    return canonical.split("-")[0] in MIG_CAPABLE_FAMILIES


# Families that can be passed through whole (kata pool): discrete data-centre boards and RTX cards
# by prefix. Grace superchip integrated GPUs (GB10 / GB200) are excluded, see
# deploy/cluster/runbooks/hardware-notes.md.
PASSTHROUGH_CAPABLE_FAMILIES = frozenset(
    {
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
    }
)


def supports_passthrough(canonical: str | None) -> bool:
    """Whether the canonical model can be passed through whole to a VM; unrecognised (None) is
    always False (fail-closed)."""
    if not canonical:
        return False
    family = canonical.split("-")[0]
    return family.startswith("RTX") or family in PASSTHROUGH_CAPABLE_FAMILIES


def model_matches(sku_model: str | None, node_model: str | None) -> bool:
    """Canonical match: equal, or the SKU names only the family while the node carries a VRAM suffix
    (A100 matches A100-80G); not the other way round."""
    if not sku_model or not node_model:
        return False
    if sku_model == node_model:
        return True
    return node_model.startswith(f"{sku_model}-")


def default_vram_gb(canonical: str | None) -> int:
    return DEFAULT_VRAM_GB.get(canonical or "", 0)
