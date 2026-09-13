from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "dados" / "biblioteca-imagens.json"
BASE = ROOT / "imagens" / "home-finish" / "bio-habitat"
ORIGINALS = BASE / "originals"
THUMBNAILS = BASE / "thumbnails"

TARGETS = [
    "101041", "101042", "101043", "101044", "101036", "101016", "101014",
    "101022", "101019", "101020", "101011", "101010", "101038", "101032",
    "101033", "101034", "101035", "101029", "101030", "101028", "101027",
    "101026", "101025", "101018", "101040", "101039", "101045", "101046",
]


def norm(ref):
    return re.sub(r"^(?:BH|MI)", "", str(ref or "").strip(), flags=re.I)


def main():
    if not MANIFEST.exists():
        raise RuntimeError(f"manifest-not-found:{MANIFEST}")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = list(manifest.get("items", []))
    failures = list(manifest.get("failures", []))
    by_key = {(x.get("f"), x.get("c"), norm(x.get("r"))): x for x in items}

    THUMBNAILS.mkdir(parents=True, exist_ok=True)
    imported = []

    for ref in TARGETS:
        op = ORIGINALS / f"{ref}.jpg"
        if not op.exists():
            raise RuntimeError(f"missing-imported-original:{ref}")

        with Image.open(op) as probe:
            probe.load()
            im = probe.convert("RGB")
        if min(im.size) < 700:
            raise RuntimeError(f"image-too-small:{ref}:{im.size[0]}x{im.size[1]}")

        tp = THUMBNAILS / f"{ref}.jpg"
        ImageOps.fit(
            im,
            (520, 520),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).save(tp, "JPEG", quality=88, optimize=True, progressive=True)

        rec = {
            "f": "Home Finish",
            "c": "BIO Habitat",
            "s": "bio-habitat",
            "r": ref,
            "source_page": "Studio_BioHabitat.pdf",
            "source_resolved": "Studio_BioHabitat.pdf — Home Finish",
            "original": str(op.relative_to(ROOT)),
            "thumbnail": str(tp.relative_to(ROOT)),
            "width": im.width,
            "height": im.height,
            "status": "ready",
            "patched": True,
            "patch_type": "official-book-pdf-extract",
        }
        by_key[("Home Finish", "BIO Habitat", ref)] = rec
        imported.append(ref)
        print(f"BIO PDF READY {ref} {im.width}x{im.height}", flush=True)

    if set(imported) != set(TARGETS):
        raise RuntimeError(f"import-incomplete:{len(imported)}/{len(TARGETS)}")

    new_items = list(by_key.values())
    new_failures = [
        x for x in failures
        if not (
            x.get("f") == "Home Finish"
            and x.get("c") == "BIO Habitat"
            and norm(x.get("r")) in set(TARGETS)
        )
    ]

    new_items.sort(key=lambda x: (x.get("f", ""), x.get("c", ""), norm(x.get("r"))))
    new_failures.sort(key=lambda x: (x.get("f", ""), x.get("c", ""), norm(x.get("r"))))

    MANIFEST.write_text(
        json.dumps(
            {
                "ready": len(new_items),
                "failed": len(new_failures),
                "items": new_items,
                "failures": new_failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"BIO PDF SUMMARY imported={len(imported)} ready={len(new_items)} failed={len(new_failures)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
