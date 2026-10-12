"""Model download feedback: tqdm/disk progress -> UI line, network-error detection,
and the local-snapshot resolver that keeps cached HF models off the network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.optional_modules import DISK_PROGRESS, format_download_line, is_network_error  # noqa: E402


def test_format_download_line():
    assert format_download_line(
        "model.safetensors:  45%|####5     | 1.20G/2.65G [00:30<00:40, 35.1MB/s]"
    ) == "model.safetensors 45% · 1.20G/2.65G · 35.1MB/s"
    assert format_download_line("Fetching 4 files:   0%|          | 0/4 [00:00<?, ?it/s]") \
        == "Fetching 4 files 0% · 0/4"
    assert format_download_line(f"{DISK_PROGRESS}1.2 GB · 35.0 MB/s") == f"{DISK_PROGRESS}1.2 GB · 35.0 MB/s"
    assert format_download_line("warnings.warn(message)") is None
    assert format_download_line("") is None


def test_is_network_error():
    assert is_network_error("OSError: We couldn't connect to 'https://hf-mirror.com' to load the files")
    assert is_network_error("LocalEntryNotFoundError: cannot find the requested files in the disk cache")
    assert is_network_error("ConnectError: [Errno 11001] getaddrinfo failed")
    assert not is_network_error("CUDA out of memory")
    assert not is_network_error("")


def test_hf_local_falls_back_to_repo_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    from core.pipelines.video_translation_pipeline import _hf_local
    assert _hf_local("nobody/not-a-cached-repo") == "nobody/not-a-cached-repo"
