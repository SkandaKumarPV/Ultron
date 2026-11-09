"""ULTRON Model Download Script"""
import hashlib
import os
import sys
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import ssl

# Model configurations
MODELS = {
    "qwen2.5-1.5b-instruct-q4_k_m": {
        "repo_id": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_bytes": 1_117_320_736,  # ~1.1 GB
        "sha256": None,  # Will verify after download
        "description": "Qwen2.5 1.5B Instruct Q4_K_M - Fast, efficient, good quality",
    },
    "qwen2.5-3b-instruct-q4_k_m": {
        "repo_id": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "filename": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_bytes": 1_900_000_000,  # ~1.9 GB
        "sha256": None,
        "description": "Qwen2.5 3B Instruct Q4_K_M - Better quality, more VRAM",
    },
    "llama-3.2-1b-instruct-q4_k_m": {
        "repo_id": "bartowski/Llama-3.2-1B-Instruct-GGUF",
        "filename": "llama-3.2-1b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/llama-3.2-1b-instruct-q4_k_m.gguf",
        "size_bytes": 750_000_000,
        "sha256": None,
        "description": "Llama 3.2 1B Instruct Q4_K_M - Small, fast",
    },
}


def download_file(url: str, dest_path: Path, expected_size: int = None, progress_callback=None) -> bool:
    """Download file with progress reporting"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Create SSL context that doesn't verify (for corporate environments)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    req = Request(url, headers={'User-Agent': 'ULTRON-ModelDownloader/1.0'})

    try:
        with urlopen(req, context=ssl_context) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            if expected_size and total_size != expected_size:
                print(f"Warning: Size mismatch. Expected {expected_size}, got {total_size}")

            downloaded = 0
            block_size = 8192

            with open(dest_path, 'wb') as f:
                while True:
                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_callback:
                        progress_callback(downloaded, total_size)

            # Verify size
            actual_size = dest_path.stat().st_size
            if expected_size and actual_size != expected_size:
                print(f"Error: Downloaded size ({actual_size}) != expected ({expected_size})")
                return False

            return True

    except HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.reason}")
        return False
    except URLError as e:
        print(f"URL Error: {e.reason}")
        return False
    except Exception as e:
        print(f"Download failed: {e}")
        return False


def verify_checksum(file_path: Path, expected_sha256: str = None) -> bool:
    """Verify file SHA256 checksum"""
    if not expected_sha256:
        print("No checksum provided, skipping verification")
        return True

    print("Verifying checksum...")
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)

    actual = sha256.hexdigest()
    if actual.lower() == expected_sha256.lower():
        print(f"Checksum verified: {actual}")
        return True
    else:
        print(f"Checksum mismatch!")
        print(f"  Expected: {expected_sha256}")
        print(f"  Actual:   {actual}")
        return False


def download_model(model_key: str, models_dir: Path = None) -> bool:
    """Download a model by key"""
    if model_key not in MODELS:
        print(f"Unknown model: {model_key}")
        print(f"Available: {', '.join(MODELS.keys())}")
        return False

    model_info = MODELS[model_key]
    models_dir = models_dir or Path(__file__).parent.parent / ".ultron" / "models"
    dest_path = models_dir / model_info["filename"]

    print(f"Downloading {model_info['description']}")
    print(f"  From: {model_info['url']}")
    print(f"  To:   {dest_path}")
    print(f"  Size: ~{model_info['size_bytes'] / 1e9:.1f} GB")

    if dest_path.exists():
        print("File already exists. Verifying...")
        if verify_checksum(dest_path, model_info.get("sha256")):
            print("Existing file is valid.")
            return True
        else:
            print("Existing file is corrupted. Re-downloading...")
            dest_path.unlink()

    def progress(downloaded, total):
        if total > 0:
            pct = downloaded / total * 100
            mb = downloaded / 1e6
            print(f"\r  Progress: {pct:.1f}% ({mb:.1f} MB)", end="", flush=True)

    success = download_file(model_info["url"], dest_path, model_info["size_bytes"], progress)
    print()  # New line after progress

    if success:
        print("Download complete. Verifying...")
        if verify_checksum(dest_path, model_info.get("sha256")):
            print(f"Success! Model saved to: {dest_path}")
            return True
        else:
            print("Checksum verification failed!")
            dest_path.unlink(missing_ok=True)
            return False
    else:
        print("Download failed!")
        dest_path.unlink(missing_ok=True)
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python download_model.py <model_key>")
        print("\nAvailable models:")
        for key, info in MODELS.items():
            print(f"  {key}: {info['description']} (~{info['size_bytes']/1e9:.1f} GB)")
        sys.exit(1)

    model_key = sys.argv[1]
    models_dir = Path(__file__).parent.parent / ".ultron" / "models"

    if download_model(model_key, models_dir):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()