"""Build the Claude skill zip from skills/dmx-research: python scripts/build_skill.py <output folder>

Writes <output folder>/dmx-research.zip with forward slashes in the entry names (Claude Desktop
rejects a zip made by PowerShell's Compress-Archive, which writes backslashes).
"""

import sys
import zipfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "dmx-research"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    target = out / "dmx-research.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(SKILL.rglob("*")):
            if f.is_file():
                z.write(f, "dmx-research/" + f.relative_to(SKILL).as_posix())
    print(f"Written: {target}")


if __name__ == "__main__":
    main()
