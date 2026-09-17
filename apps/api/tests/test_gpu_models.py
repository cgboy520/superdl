"""Exhaustive model normalisation: five source formats → canonical; matching semantics."""

from app.core.gpu_models import canonical_gpu_model, model_matches


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
        assert canonical_gpu_model("NVIDIA GB10") == "GB10"

    def test_lspci_tail(self):
        assert (
            canonical_gpu_model("NVIDIA Corporation AD102 [GeForce RTX 4090] (rev a1)") == "RTX4090"
        )
        assert (
            canonical_gpu_model("NVIDIA Corporation GA100 [A100 SXM4 80GB] (rev a1)") == "A100-80G"
        )

    def test_cmp_mining_series(self):
        """CMP series: the nvidia-smi generic name cannot be normalised, the model comes from the
        lspci bracket name or a manual SKU."""
        assert canonical_gpu_model("NVIDIA Graphics Device") is None
        assert canonical_gpu_model("NVIDIA Corporation GA100 [CMP 170HX] (rev a1)") == "CMP170HX"
        assert canonical_gpu_model("CMP 170HX") == "CMP170HX"
        assert canonical_gpu_model("CMP170HX") == "CMP170HX"
        assert canonical_gpu_model("CMP-90HX") == "CMP90HX"

    def test_gfd_label(self):
        assert canonical_gpu_model("NVIDIA-GeForce-RTX-4090") == "RTX4090"
        assert canonical_gpu_model("NVIDIA-A100-SXM4-80GB") == "A100-80G"
        assert canonical_gpu_model("NVIDIA-GB10") == "GB10"
        assert canonical_gpu_model("Tesla-V100-SXM2-32GB") == "V100-32G"

    def test_unknown_returns_none(self):
        assert canonical_gpu_model("Moore Threads MTT S4000") is None
        assert canonical_gpu_model("") is None
        assert canonical_gpu_model(None) is None


class TestMatch:
    def test_exact_and_prefix(self):
        assert model_matches("RTX4090", "RTX4090")
        assert model_matches("A100", "A100-80G")
        assert not model_matches("A100-80G", "A100")
        assert not model_matches("RTX4090", "RTX4090D")
        assert not model_matches(None, "RTX4090")
        assert not model_matches("RTX4090", None)
