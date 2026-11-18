"""Build an LNbits release archive and optional explicit-release manifest."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", help="URL of the directory that will serve the archive"
    )
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    config = json.loads((source / "config.json").read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"arguswallet-{config['version']}.zip"
    files = [
        source / f
        for f in (
            "__init__.py",
            "api.py",
            "bitcoin.py",
            "explorer.py",
            "migrations.py",
            "config.json",
            "README.md",
            "LICENSE",
        )
    ]
    for directory in ("static", "templates"):
        files.extend(
            p
            for p in (source / directory).rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )
    with ZipFile(archive, "w") as release:
        for path in sorted(files):
            name = "arguswallet/" + path.relative_to(source).as_posix()
            release.writestr(
                ZipInfo(name), path.read_bytes(), compress_type=ZIP_DEFLATED
            )
    print(archive)
    if args.base_url:
        entry = {
            k: config[k]
            for k in ("name", "version", "short_description", "min_lnbits_version")
        }
        entry.update(
            id="arguswallet",
            archive=args.base_url.rstrip("/") + "/" + archive.name,
            hash=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )
        (args.output / "manifest.json").write_text(
            json.dumps({"extensions": [entry]}, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
