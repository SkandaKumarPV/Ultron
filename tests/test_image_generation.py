import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultron.image.generator import ImageBackend, ImageGenerator


def test_auto_backend_uses_procedural_without_cuda(monkeypatch, tmp_path):
    class FakeTorch:
        class cuda:
            @staticmethod
            def is_available():
                return False

    class FakeDiffusers:
        DiffusionPipeline = object

    monkeypatch.setitem(sys.modules, "torch", FakeTorch)
    monkeypatch.setitem(sys.modules, "diffusers", FakeDiffusers)
    monkeypatch.setattr("ultron.image.generator._resolve_output_dir", lambda: tmp_path)

    generator = ImageGenerator()
    generator.initialize()
    generator.set_backend(ImageBackend.AUTO.value)

    result = generator.generate("tree", backend="auto", seed=1)

    assert result.success
    assert result.backend == ImageBackend.PROCEDURAL.value
    assert result.path is not None
    assert "out-of-memory" in (result.error or "")
