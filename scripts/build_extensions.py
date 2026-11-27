"""Build the relay extensions: python scripts/build_extensions.py <world>... [--out dist] [--version 1.0.0]
   or: python scripts/build_extensions.py --config config.toml [--out dist]   (every world of the config)

Writes <out>/dmx-<world>.mcpb for each world: a zip (forward-slash entry names) holding manifest.json
and server/dmx-relay.ps1.
"""

import argparse
import sys
import zipfile
from pathlib import Path

RELAY = Path(__file__).resolve().parents[1] / "relay"


def build(worlds: list[str], out: Path, version: str = "1.0.0") -> list[Path]:
    template = (RELAY / "manifest.template.json").read_text(encoding="utf-8")
    script = (RELAY / "dmx-relay.ps1").read_bytes()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for world in worlds:
        manifest = template.replace("@WORLD@", world).replace("@VERSION@", version)
        target = out / f"dmx-{world}.mcpb"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", manifest.encode("utf-8"))
            z.writestr("server/dmx-relay.ps1", script)
        written.append(target)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worlds", nargs="*")
    ap.add_argument("--config", help="config.toml: build one extension per world it defines")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--version", default="1.0.0")
    a = ap.parse_args()
    worlds = list(a.worlds)
    if a.config:
        from dmx_docs.central import world_names
        worlds += world_names(Path(a.config))
    if not worlds:
        sys.exit(__doc__)
    for t in build(worlds, Path(a.out), a.version):
        print(f"Written: {t}")


if __name__ == "__main__":
    main()
