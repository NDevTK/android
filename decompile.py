"""Discover, download, and decompile APKs from Google-related developers on APKCombo."""

import argparse
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"
APKS_DIR = ROOT / "apks"
SOURCES_DIR = ROOT / "sources"
VERSIONS_DIR = ROOT / "versions"

BASE = "https://apkcombo.com"

DEVELOPERS = [
    "Google LLC",
    "Developed with Google",
    "Research at Google",
    "Red Hot Labs",
    "Google Samples",
    "Fitbit LLC",
    "Nest Labs Inc.",
    "Waymo LLC",
    "Waze",
]


def get_jadx_path():
    if platform.system() == "Windows":
        return TOOLS_DIR / "jadx" / "bin" / "jadx.bat"
    return TOOLS_DIR / "jadx" / "bin" / "jadx"


def get_jdk_home():
    """Return the path to the locally installed JDK, or None."""
    jdk_dir = TOOLS_DIR / "jdk"
    if not jdk_dir.exists():
        return None
    subdirs = [d for d in jdk_dir.iterdir() if d.is_dir()]
    if subdirs:
        return subdirs[0]
    return None


def get_stored_version(package):
    """Read the previously stored version for a package."""
    ver_file = VERSIONS_DIR / f"{package}.version"
    if ver_file.exists():
        return ver_file.read_text().strip()
    return None


def save_version(package, version):
    """Store the current version for a package."""
    ver_file = VERSIONS_DIR / f"{package}.version"
    ver_file.write_text(version)


# ---------------------------------------------------------------------------
# Discovery: scrape developer pages to find all apps
# ---------------------------------------------------------------------------

def discover_apps(session, developers=None):
    """Scrape APKCombo developer pages to discover all apps.

    Returns list of dicts: {"app_path": "/slug/pkg.id", "package": "pkg.id", "name": "App Name", "developer": "Dev"}
    """
    if developers is None:
        developers = DEVELOPERS

    all_apps = []
    seen_packages = set()

    for dev in developers:
        dev_url = f"{BASE}/developer/{quote(dev)}/"
        page = 1

        while True:
            url = dev_url if page == 1 else f"{dev_url}?page={page}"
            print(f"  Fetching {dev} (page {page})...")

            try:
                r = session.get(url)
                r.raise_for_status()
            except Exception as e:
                print(f"    ERROR: {e}")
                break

            # Extract app links: href="/app-slug/com.package.id/"
            # Package IDs: 2+ segments separated by dots, lowercase + digits + underscores
            app_links = re.findall(
                r'href="(/[^"]+/([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)/)"',
                r.text,
            )

            if not app_links:
                if page == 1:
                    print(f"    No apps found for {dev}")
                break

            # Extract app names: find <p> tags that follow each app link
            # Build a mapping from app_path to name
            names = {}
            for app_path, pkg in app_links:
                # Find the name in the <p> tag after this href
                pattern = re.escape(app_path) + r'/"?\s*[^>]*>[\s\S]*?<p[^>]*>([^<]+)</p>'
                m = re.search(pattern, r.text)
                if m:
                    names[pkg] = m.group(1).strip()

            count = 0
            for app_path, package in app_links:
                if package not in seen_packages:
                    seen_packages.add(package)
                    all_apps.append({
                        "app_path": app_path.rstrip("/"),
                        "package": package,
                        "name": names.get(package, package),
                        "developer": dev,
                    })
                    count += 1

            print(f"    Found {count} new apps")

            # Check for next page
            next_page = f"?page={page + 1}"
            if next_page in r.text:
                page += 1
                time.sleep(0.5)
            else:
                break

        time.sleep(0.5)

    return all_apps


# ---------------------------------------------------------------------------
# Version checking: fetch app page to get current version
# ---------------------------------------------------------------------------

def check_version(session, app_path):
    """Fetch an app page and extract the current version string."""
    url = f"{BASE}{app_path}"
    try:
        r = session.get(url)
        r.raise_for_status()
    except Exception as e:
        print(f"    Version check failed: {e}")
        return None

    # Try structured data first: "softwareVersion": "..."
    match = re.search(r'"softwareVersion"\s*:\s*"([^"]+)"', r.text)
    if match:
        return match.group(1)

    # Fallback: version in page header area
    match = re.search(r'class="version"[^>]*>([^<]+)<', r.text)
    if match:
        return match.group(1).strip()

    return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_apk(session, app_path, package):
    """Download APK from APKCombo. Returns path to downloaded file or None."""
    download_dir = APKS_DIR / package
    if download_dir.exists():
        shutil.rmtree(download_dir)
    download_dir.mkdir(parents=True)

    # Fetch the download page
    try:
        r = session.get(f"{BASE}{app_path}/download/apk")
        r.raise_for_status()
    except Exception as e:
        print(f"    Download page failed: {e}")
        return None

    # Two download URL patterns on APKCombo:
    #   /r2?u=<encoded-r2-url>  — direct Cloudflare R2 storage
    #   /d?u=<base64>           — PureAPK CDN redirect
    r2_links = re.findall(r'href="(/r2\?u=[^"]+)"', r.text)
    d_links = re.findall(r'href="(https://apkcombo\.com/d\?u=[^"]+)"', r.text)

    if r2_links:
        dl_url = BASE + html.unescape(r2_links[0])
    elif d_links:
        dl_url = html.unescape(d_links[0])
    else:
        print(f"    No download links found")
        return None

    # Download the file
    print(f"    Downloading...")
    try:
        r = session.get(dl_url, timeout=300)
        r.raise_for_status()
    except Exception as e:
        print(f"    Download failed: {e}")
        return None

    # Determine filename
    cd = r.headers.get("content-disposition", "")
    fname_match = re.search(r'filename="?([^";\n]+)"?', cd)
    if fname_match:
        fname = fname_match.group(1).strip()
    else:
        url_path = r.url.split("?")[0].split("/")[-1]
        fname = url_path if "." in url_path else f"{package}.apk"

    fname = re.sub(r'[<>:"/\\|?*]', "_", fname)
    dest = download_dir / fname

    with open(dest, "wb") as f:
        f.write(r.content)

    size_mb = dest.stat().st_size / (1024 * 1024)
    if size_mb < 0.5:
        print(f"    File too small ({size_mb:.2f} MB), likely error page")
        dest.unlink()
        return None

    print(f"    Downloaded: {fname} ({size_mb:.1f} MB)")
    return dest


# ---------------------------------------------------------------------------
# Decompilation
# ---------------------------------------------------------------------------

def count_dex_classes(apk_path):
    """Count total classes in all DEX files inside an APK/XAPK."""
    import struct
    import zipfile

    total = 0
    try:
        with zipfile.ZipFile(apk_path, "r") as zf:
            for name in zf.namelist():
                if name.endswith(".dex"):
                    data = zf.read(name)
                    if len(data) >= 96 and data[:4] == b"dex\n":
                        total += struct.unpack_from("<I", data, 96)[0]
                elif name.endswith(".apk"):
                    import io
                    nested = io.BytesIO(zf.read(name))
                    try:
                        with zipfile.ZipFile(nested, "r") as inner:
                            for inner_name in inner.namelist():
                                if inner_name.endswith(".dex"):
                                    dex_data = inner.read(inner_name)
                                    if len(dex_data) >= 96 and dex_data[:4] == b"dex\n":
                                        total += struct.unpack_from("<I", dex_data, 96)[0]
                    except Exception:
                        pass
    except Exception:
        pass
    return total


def count_java_files_fast(directory):
    """Count .java files using os.scandir (much faster than rglob)."""
    count = 0
    try:
        for entry in os.scandir(directory):
            if entry.is_dir(follow_symlinks=False):
                count += count_java_files_fast(entry.path)
            elif entry.name.endswith(".java"):
                count += 1
    except PermissionError:
        pass
    return count


def monitor_decompile_progress(output_dir, total_classes, stop_event):
    """Monitor output directory and print progress percentage."""
    last_count = 0
    while not stop_event.is_set():
        try:
            count = count_java_files_fast(output_dir)
            if count != last_count:
                if total_classes > 0:
                    pct = min(count / total_classes * 100, 99.9)
                    print(f"\r    Progress: {pct:5.1f}% ({count}/{total_classes} classes)", end="", flush=True)
                else:
                    print(f"\r    Decompiled {count} classes...", end="", flush=True)
                last_count = count
        except Exception:
            pass
        stop_event.wait(10)


def decompile_apk(apk_path, package, threads):
    """Decompile APK using jadx. Returns True on success."""
    jadx = get_jadx_path()
    output_dir = SOURCES_DIR / package

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    total_classes = count_dex_classes(apk_path)
    if total_classes:
        print(f"    Found {total_classes} classes in DEX")

    env = os.environ.copy()
    env["JAVA_OPTS"] = "-Xmx8g"

    jdk_home = get_jdk_home()
    if jdk_home:
        env["JAVA_HOME"] = str(jdk_home)
        env["PATH"] = str(jdk_home / "bin") + os.pathsep + env.get("PATH", "")

    cmd = [
        str(jadx),
        "--threads-count", str(threads),
        "--output-dir", str(output_dir),
        "--log-level", "error",
        "--deobf",
        "--deobf-use-sourcename",
        "--deobf-min", "3",
        "--deobf-res",
        str(apk_path),
    ]
    print(f"    Decompiling with jadx ({threads} threads)...")

    stop_event = threading.Event()
    monitor = threading.Thread(
        target=monitor_decompile_progress,
        args=(output_dir, total_classes, stop_event),
        daemon=True,
    )
    monitor.start()

    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=1800,
    )

    stop_event.set()
    monitor.join()
    elapsed = time.time() - start

    print(f"\r    Progress: 100.0%{' ' * 40}")

    if result.stdout:
        for line in result.stdout.strip().splitlines()[-5:]:
            print(f"    {line}")
    if result.stderr:
        for line in result.stderr.strip().splitlines()[-5:]:
            print(f"    {line}")

    java_files = sum(1 for _ in output_dir.rglob("*.java"))
    xml_files = sum(1 for _ in output_dir.rglob("*.xml"))
    print(f"    Output: {java_files} Java files, {xml_files} XML files ({elapsed:.0f}s)")

    if not java_files:
        print(f"    ERROR: Decompilation produced no Java files")
        return False

    return True


# ---------------------------------------------------------------------------
# Process a single app
# ---------------------------------------------------------------------------

def process_app(session, app, force=False, threads=4):
    """Process a single app: check version, download if changed, decompile."""
    package = app["package"]
    app_path = app["app_path"]

    # Check current version on APKCombo
    print(f"  Checking version...")
    current_version = check_version(session, app_path)
    if not current_version:
        print(f"  Could not determine version, downloading anyway...")
    else:
        stored_version = get_stored_version(package)
        print(f"  Current:  {current_version}")
        print(f"  Stored:   {stored_version or '(none)'}")

        if stored_version == current_version and not force:
            print(f"  Version unchanged, skipping.")
            return True

    # Download
    print(f"  Downloading APK...")
    apk_path = download_apk(session, app_path, package)
    if not apk_path:
        return False

    # Decompile
    print(f"  Decompiling...")
    success = decompile_apk(apk_path, package, threads)

    if success and current_version:
        save_version(package, current_version)
        print(f"  Version saved.")

    # Clean up APK
    apk_dir = APKS_DIR / package
    if apk_dir.exists():
        shutil.rmtree(apk_dir)
        print(f"  Cleaned up downloaded APK")

    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    from curl_cffi import requests as cffi_requests

    parser = argparse.ArgumentParser(description="Discover, download, and decompile APKs from Google developers")
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-decompile even if version unchanged",
    )
    parser.add_argument(
        "--developer", type=str,
        help="Process apps from a single developer (e.g., 'Google LLC')",
    )
    parser.add_argument(
        "--package", type=str,
        help="Process a single package (e.g., com.google.android.gm)",
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_apps",
        help="List discovered apps and versions without downloading",
    )
    args = parser.parse_args()

    # Verify tools are installed
    if not args.list_apps and not get_jadx_path().exists():
        print("ERROR: jadx not found. Run: python setup.py")
        return 1

    # Ensure directories exist
    for d in [APKS_DIR, SOURCES_DIR, VERSIONS_DIR]:
        d.mkdir(exist_ok=True)

    session = cffi_requests.Session(impersonate="chrome")
    threads = os.cpu_count() or 4

    # Determine which developers to scrape
    if args.developer:
        matching = [d for d in DEVELOPERS if d.lower() == args.developer.lower()]
        if not matching:
            print(f"ERROR: Unknown developer '{args.developer}'")
            print(f"Valid developers: {', '.join(DEVELOPERS)}")
            return 1
        devs = matching
    else:
        devs = DEVELOPERS

    # Discover apps
    print("=== Discovering apps ===")
    apps = discover_apps(session, devs)
    print(f"\nDiscovered {len(apps)} apps")

    # Filter to single package if requested
    if args.package:
        apps = [a for a in apps if a["package"] == args.package]
        if not apps:
            print(f"ERROR: Package '{args.package}' not found in discovered apps")
            return 1

    # List mode: show apps and exit
    if args.list_apps:
        print(f"\n{'Package':<55} {'Version':<40} {'Developer'}")
        print("-" * 130)
        for app in sorted(apps, key=lambda a: a["developer"]):
            stored = get_stored_version(app["package"]) or ""
            print(f"  {app['package']:<53} {stored:<40} {app['developer']}")
        return 0

    # Process apps
    print(f"\nCPUs: {threads}")
    results = {}
    for i, app in enumerate(apps, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(apps)}] {app['name']} ({app['package']})")
        print(f"{'='*60}")
        results[app["package"]] = process_app(session, app, force=args.force, threads=threads)
        time.sleep(0.5)

    # Summary
    print(f"\n{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    changed = sum(1 for ok in results.values() if ok)
    failed = sum(1 for ok in results.values() if not ok)
    for pkg, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {pkg}: {status}")
    print(f"\n  {changed} succeeded, {failed} failed, {len(apps)} total")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
