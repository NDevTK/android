"""Search decompiled sources using a SQLite FTS5 index."""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES_DIR = ROOT / "sources"
DB_PATH = ROOT / "index.db"


def create_db(conn):
    """Create the FTS5 virtual table."""
    conn.execute("DROP TABLE IF EXISTS files")
    conn.execute("""
        CREATE VIRTUAL TABLE files USING fts5(
            path,
            package,
            filename,
            content,
            tokenize='unicode61'
        )
    """)


def build_index(packages=None):
    """Index all source files into SQLite FTS5."""
    if not SOURCES_DIR.exists():
        print("ERROR: sources/ directory not found. Run decompile.py first.")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    create_db(conn)

    count = 0
    errors = 0
    start = time.time()

    source_dirs = []
    if packages:
        for pkg in packages:
            d = SOURCES_DIR / pkg
            if d.exists():
                source_dirs.append((pkg, d))
            else:
                print(f"  Warning: {pkg} not found in sources/")
    else:
        for d in SOURCES_DIR.iterdir():
            if d.is_dir():
                source_dirs.append((d.name, d))

    for pkg_name, pkg_dir in source_dirs:
        print(f"  Indexing {pkg_name}...")
        pkg_count = 0
        batch = []

        for fpath in pkg_dir.rglob("*"):
            if not fpath.is_file():
                continue
            if fpath.suffix not in (".java", ".xml", ".json", ".properties"):
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                errors += 1
                continue

            rel_path = str(fpath.relative_to(SOURCES_DIR))
            batch.append((rel_path, pkg_name, fpath.name, content))
            pkg_count += 1

            if len(batch) >= 500:
                conn.executemany(
                    "INSERT INTO files (path, package, filename, content) VALUES (?, ?, ?, ?)",
                    batch,
                )
                batch.clear()

        if batch:
            conn.executemany(
                "INSERT INTO files (path, package, filename, content) VALUES (?, ?, ?, ?)",
                batch,
            )

        count += pkg_count
        print(f"    {pkg_count} files")

    conn.commit()
    conn.close()

    elapsed = time.time() - start
    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"\nIndexed {count} files in {elapsed:.1f}s ({size_mb:.1f} MB database)")
    if errors:
        print(f"  {errors} files skipped due to read errors")
    return True


def search(query, package=None, filename=None, limit=50):
    """Search the index."""
    if not DB_PATH.exists():
        print("ERROR: No index found. Run: python search.py --build")
        return

    conn = sqlite3.connect(str(DB_PATH))

    # Build FTS5 query
    fts_parts = [query]
    if package:
        fts_parts.append(f'package:"{package}"')
    if filename:
        fts_parts.append(f'filename:"{filename}"')

    fts_query = " ".join(fts_parts)

    sql = """
        SELECT path, snippet(files, 3, '>>>', '<<<', '...', 40) as snippet,
               rank
        FROM files
        WHERE files MATCH ?
        ORDER BY rank
        LIMIT ?
    """

    start = time.time()
    try:
        rows = conn.execute(sql, (fts_query, limit)).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Search error: {e}")
        print("Tip: Use double quotes for exact phrases, e.g.: '\"exact phrase\"'")
        conn.close()
        return

    elapsed = time.time() - start

    if not rows:
        print("No results found.")
        conn.close()
        return

    print(f"\n{len(rows)} results ({elapsed*1000:.0f}ms):\n")
    for path, snippet, rank in rows:
        print(f"  {path}")
        # Clean up and indent snippet
        for line in snippet.strip().splitlines():
            print(f"    {line.strip()}")
        print()

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Search decompiled Android sources")
    parser.add_argument("query", nargs="?", help="Search query (FTS5 syntax)")
    parser.add_argument("--build", action="store_true", help="Build/rebuild the search index")
    parser.add_argument("--package", "-p", type=str, help="Filter by package name")
    parser.add_argument("--filename", "-f", type=str, help="Filter by filename")
    parser.add_argument("--limit", "-n", type=int, default=50, help="Max results (default: 50)")
    args = parser.parse_args()

    if args.build:
        print("Building search index...")
        build_index()
        return 0

    if not args.query:
        parser.print_help()
        return 1

    search(args.query, package=args.package, filename=args.filename, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
