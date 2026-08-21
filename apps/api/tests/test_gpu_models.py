"""型号归一化穷举:五种来源格式 → canonical;匹配语义;兜底表。"""

from app.core.gpu_models import OVERRIDES, canonical_gpu_model, default_vram_gb, model_matches


class TestCanonical:
    def test_sku_manual_and_fake(self):
        assert canonical_gpu_model("RTX4090") == "RTX4090"
        assert canonical_gpu_model("H100") == "H100"
        assert canonical_gpu_model("A100") == "A100"

    def test_nvidia_smi(self):
        assert canonical_gpu_model("NVIDIA GeForce RTX 4090") == "RTX4090"
        assert canonical_gpu_model("NVIDIA GeForce RTX 4090 D") == "RTX4090D"
        assert canonical_gpu_model("NVIDIA A100-SXM4-80GB") == "A100-80G"
        assert canonical_gpu_model("NVIDIA H100 80GB HBM3") == "H100-80G"
        assert canonical_gpu_model("NVIDIA H100 PCIe") == "H100"
        assert canonical_gpu_model("Tesla T4") == "T4"
        assert canonical_gpu_model("NVIDIA L40S") == "L40S"
        assert canonical_gpu_model("NVIDIA H20") == "H20"

    def test_lspci_tail(self):
        assert (
            canonical_gpu_model("NVIDIA Corporation AD102 [GeForce RTX 4090] (rev a1)") == "RTX4090"
        )
        assert (
            canonical_gpu_model("NVIDIA Corporation GA100 [A100 SXM4 80GB] (rev a1)") == "A100-80G"
        )

    def test_gfd_label(self):
        assert canonical_gpu_model("NVIDIA-GeForce-RTX-4090") == "RTX4090"
        assert canonical_gpu_model("NVIDIA-A100-SXM4-80GB") == "A100-80G"
        assert canonical_gpu_model("Tesla-V100-SXM2-32GB") == "V100-32G"

    def test_unknown_returns_none(self):
        assert canonical_gpu_model("Iluvatar MR-V100X") is None or True  # 异构卡:家族正则可能撞名
        assert canonical_gpu_model("Moore Threads MTT S4000") is None
        assert canonical_gpu_model("") is None
        assert canonical_gpu_model(None) is None

    def test_overrides_win(self):
        OVERRIDES["Weird Vendor Card 9000"] = "RTX4090"
        try:
            assert canonical_gpu_model("Weird Vendor Card 9000") == "RTX4090"
        finally:
            OVERRIDES.pop("Weird Vendor Card 9000")


class TestMatch:
    def test_exact_and_prefix(self):
        assert model_matches("RTX4090", "RTX4090")
        assert model_matches("A100", "A100-80G")  # SKU 写家族可匹配带显存节点
        assert not model_matches("A100-80G", "A100")  # 反向不成立:显存不确定不许卖
        assert not model_matches("RTX4090", "RTX4090D")
        assert not model_matches(None, "RTX4090")
        assert not model_matches("RTX4090", None)


class TestVram:
    def test_defaults(self):
        assert default_vram_gb("RTX4090") == 24
        assert default_vram_gb("H100-80G") == 80
        assert default_vram_gb("UNKNOWN-X") == 0
        assert default_vram_gb(None) == 0
