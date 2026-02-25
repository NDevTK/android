"""One-time setup: downloads jadx and JDK to tools/ directory."""

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"

JADX_VERSION = "1.5.4"
JADX_URL = f"https://github.com/skylot/jadx/releases/download/v{JADX_VERSION}/jadx-{JADX_VERSION}.zip"

# Adoptium JDK 25 LTS — redirect URL always points to latest GA release
JDK_URLS = {
    "Windows": "https://api.adoptium.net/v3/binary/latest/25/ga/windows/x64/jdk/hotspot/normal/eclipse",
    "Linux": "https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jdk/hotspot/normal/eclipse",
    "Darwin": "https://api.adoptium.net/v3/binary/latest/25/ga/mac/x64/jdk/hotspot/normal/eclipse",
}


def get_jdk_home():
    """Return the path to the locally installed JDK, or None."""
    jdk_dir = TOOLS_DIR / "jdk"
    if not jdk_dir.exists():
        return None
    subdirs = [d for d in jdk_dir.iterdir() if d.is_dir()]
    if subdirs:
        return subdirs[0]
    return None


def check_java():
    """Check if Java is available (system or local install)."""
    jdk_home = get_jdk_home()
    if jdk_home:
        java_bin = jdk_home / "bin" / ("java.exe" if platform.system() == "Windows" else "java")
        if java_bin.exists():
            result = subprocess.run([str(java_bin), "-version"], capture_output=True, text=True)
            output = result.stderr + result.stdout
            print(f"  Java found (local): {output.splitlines()[0].strip()}")
            return True

    try:
        result = subprocess.run(["java", "-version"], capture_output=True, text=True)
        output = result.stderr + result.stdout
        print(f"  Java found (system): {output.splitlines()[0].strip()}")
        return True
    except FileNotFoundError:
        return False


def setup_jdk():
    """Download and extract Adoptium JDK 25."""
    print("\n[1/3] Installing JDK 25...")

    if check_java():
        return True

    system = platform.system()
    url = JDK_URLS.get(system)
    if not url:
        print(f"  ERROR: No JDK URL for {system}. Install Java 11+ manually.")
        return False

    jdk_dir = TOOLS_DIR / "jdk"
    jdk_dir.mkdir(parents=True, exist_ok=True)

    ext = ".zip" if system == "Windows" else ".tar.gz"
    archive_path = TOOLS_DIR / f"jdk{ext}"

    print(f"  Downloading JDK 25 from Adoptium...")
    download_file(url, archive_path)

    print(f"  Extracting to {jdk_dir}...")
    if ext == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(jdk_dir)
    else:
        import tarfile
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(jdk_dir)

    archive_path.unlink()

    jdk_home = get_jdk_home()
    if jdk_home:
        print(f"  JDK installed to {jdk_home}")
        return True

    print("  ERROR: JDK extraction failed")
    return False


def download_file(url, dest):
    """Download a file with progress indication."""
    print(f"  Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"  Saved to {dest} ({size_mb:.1f} MB)")


def setup_jadx():
    """Download and extract jadx."""
    print("\n[2/3] Installing jadx...")
    jadx_dir = TOOLS_DIR / "jadx"

    if jadx_dir.exists() and any(jadx_dir.iterdir()):
        print(f"  Already exists at {jadx_dir}, skipping (delete to re-download)")
        return True

    zip_path = TOOLS_DIR / "jadx.zip"
    download_file(JADX_URL, zip_path)

    print(f"  Extracting to {jadx_dir}...")
    jadx_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(jadx_dir)
    zip_path.unlink()

    if platform.system() != "Windows":
        for f in (jadx_dir / "bin").iterdir():
            f.chmod(0o755)

    return True


def create_directories():
    """Create the project directory structure."""
    for d in ["apks", "sources", "versions", "tools"]:
        (ROOT / d).mkdir(exist_ok=True)


def setup_pip_deps():
    """Install required Python packages."""
    print("\n[3/3] Installing Python dependencies...")
    try:
        import curl_cffi  # noqa: F401
        print("  curl_cffi already installed")
        return True
    except ImportError:
        pass

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "curl_cffi"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("  curl_cffi installed")
        return True

    print(f"  ERROR: pip install failed: {result.stderr.strip()}")
    return False


def main():
    print("=== APK Decompiler Setup ===")
    print(f"Root: {ROOT}")

    create_directories()

    java_ok = setup_jdk()
    jadx_ok = setup_jadx()
    pip_ok = setup_pip_deps()

    print("\n=== Summary ===")
    print(f"  Java:     {'OK' if java_ok else 'MISSING'}")
    print(f"  jadx:     {'OK' if jadx_ok else 'FAILED'}")
    print(f"  curl_cffi: {'OK' if pip_ok else 'FAILED'}")

    if java_ok and jadx_ok and pip_ok:
        print("\nSetup complete! Run: python decompile.py")
        return 0
    else:
        print("\nSetup incomplete. Fix the issues above and re-run.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
