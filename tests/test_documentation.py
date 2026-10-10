import asyncio

import pytest

from conftest import make_png
from dmx_docs import store
from dmx_docs.extract import extract_file, md_pictures, pymupdf
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools, resolve_md_link

quiet = lambda *a, **k: None  # noqa: E731

PALOMA_FR = """#
## Description du fonctionnement d'une cellule robot

Le robot reçoit les produits de l’équipement amont dans un flux d'entrée.

![🖼 Dessus de la Paloma ](O:/ASA/Documentation/DOC_Machines/Source/1000_Introduction/1301_Images%20PALOMA/top.png)

![](Images/side.png)

Le flux de production peut être décomposé selon les éléments suivants:

- Le flux d’entrée des produits.
- La cellule robot.
"""

PALOMA_DE = """## Funktionsbeschreibung einer Roboterzelle

Der Roboter der Paloma übernimmt die Produkte vom vorgelagerten Gerät.
"""

ENTRETIEN_FR = """## Remplacement du réducteur

Contrôler le niveau d'huile du réducteur toutes les 500 heures.

![Réducteur](O:/ASA/Documentation/DOC_Machines/Source/6000_Entretien/missing.png)
"""


@pytest.fixture
def manual(tmp_path, make_cfg):
    """A miniature of the machine manual: chapter folders, FR/DE files, pictures next to them."""
    root = tmp_path / "DOC_Machines" / "Source"
    intro = root / "1000_Introduction"
    (intro / "1301_Images PALOMA").mkdir(parents=True)
    (intro / "Images").mkdir()
    (root / "6000_Entretien").mkdir()
    (root / "OldVersions").mkdir()
    make_png(intro / "1301_Images PALOMA" / "top.png", (255, 0, 0))
    make_png(intro / "Images" / "side.png", (0, 0, 255))
    (intro / "1301_Description_PALOMA_FR_V00.md").write_text(PALOMA_FR, encoding="utf-8")
    (intro / "1301_Description_PALOMA_DE_V00.md").write_text(PALOMA_DE, encoding="utf-8")
    (root / "6000_Entretien" / "6400_Remplacement_reducteur_FR_V00.md").write_text(ENTRETIEN_FR, encoding="utf-8")
    (root / "OldVersions" / "ancien_FR.md").write_text("## Ancienne version du réducteur\n\nTexte périmé.",
                                                        encoding="utf-8")
    cfg = make_cfg(root, extensions=[".md", ".pdf", ".docx"], exclude=["~$*", ".*", "OldVersions"],
                   world="documentation", world_title="Documentation", profile="documentation")
    run_index(cfg, progress=quiet)
    return cfg, root


def test_markdown_text_with_numbered_pictures(manual):
    _, root = manual
    r = extract_file(str(root / "1000_Introduction" / "1301_Description_PALOMA_FR_V00.md"))
    assert r.status == "ok" and r.n_pages == 1
    text = dict(r.pages)[1]
    assert r.title == "Description du fonctionnement d'une cellule robot"
    assert "[Image 1: Dessus de la Paloma]" in text and "[Image 2]" in text
    assert "O:/ASA" not in text and "%20" not in text          # link targets are not indexed
    assert "La cellule robot." in text


def test_markdown_pictures_in_document_order():
    pics = md_pictures(PALOMA_FR)
    assert [c for c, _ in pics] == ["Dessus de la Paloma", ""]
    assert pics[1][1] == "Images/side.png"
    assert md_pictures('![a](C:/x y/z.png "title")') == [("a", "C:/x y/z.png")]   # unencoded space, title dropped


def test_chapter_and_language_facets_and_filters(manual):
    cfg, _ = manual
    con = store.connect(cfg.db_path)
    rows = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT d.name, f.category, f.language FROM docs d JOIN doc_facets f ON f.doc_id = d.id")}
    con.close()
    assert rows["1301_Description_PALOMA_FR_V00.md"] == ("1000_Introduction", "FR")
    assert rows["1301_Description_PALOMA_DE_V00.md"] == ("1000_Introduction", "DE")
    assert rows["6400_Remplacement_reducteur_FR_V00.md"] == ("6000_Entretien", "FR")
    assert "ancien_FR.md" not in rows                              # old versions are excluded
    t = DocTools(cfg)
    both = t.search("Paloma", mode="keyword")
    assert "PALOMA_FR" in both and "PALOMA_DE" in both
    out = t.search("Paloma", mode="keyword", language="fr")
    assert "PALOMA_FR" in out and "PALOMA_DE" not in out
    assert "category 1000_Introduction · language FR" in out
    assert "Remplacement_reducteur" in t.search("réducteur", mode="keyword", category="6000_Entretien")


def test_view_and_export_markdown_pictures(manual, tmp_path):
    cfg, root = manual
    t = DocTools(cfg)
    md = str(root / "1000_Introduction" / "1301_Description_PALOMA_FR_V00.md")
    caption, data, _ = t.view_page(md, image=1)       # absolute O:/ link, rebuilt from the indexed root
    assert "picture 1 of 2" in caption and "Dessus de la Paloma" in caption
    assert tuple(pymupdf.Pixmap(data).pixel(5, 5)) == (255, 0, 0)
    _, data, _ = t.view_page(md, image=2)             # relative link
    assert tuple(pymupdf.Pixmap(data).pixel(5, 5)) == (0, 0, 255)
    with pytest.raises(ValueError, match="between 1 and 2"):
        t.view_page(md, image=3)
    t.cfg.export_dir = str(tmp_path / "exports")
    out = t.export_image(md, image=2)
    target = tmp_path / "exports" / "1301_Description_PALOMA_FR_V00_img2.png"
    assert str(target) in out and target.read_bytes()[:4] == b"\x89PNG"


def test_broken_or_outside_picture_links(manual, tmp_path):
    cfg, root = manual
    t = DocTools(cfg)
    with pytest.raises(FileNotFoundError, match="Picture 1 .* not found"):
        t.view_page(str(root / "6000_Entretien" / "6400_Remplacement_reducteur_FR_V00.md"), image=1)
    with pytest.raises(ValueError, match="No picture"):
        t.view_page(str(root / "1000_Introduction" / "1301_Description_PALOMA_DE_V00.md"))
    outside = tmp_path / "secret.png"
    make_png(outside, (0, 255, 0))
    md = root / "6000_Entretien" / "evil_FR.md"
    md.write_text(f"## x\n\nTexte\n\n![x]({outside.as_posix()})\n", encoding="utf-8")
    run_index(cfg, progress=quiet)
    with pytest.raises(ValueError, match="outside the indexed folders"):
        t.view_page(str(md), image=1)


def test_link_resolution_rebases_an_unmapped_drive(tmp_path):
    root = tmp_path / "Share" / "DOC_Machines" / "Source"
    (root / "Img").mkdir(parents=True)
    make_png(root / "Img" / "a b.png", (1, 2, 3))
    md = root / "page_FR.md"
    found = resolve_md_link("Q:/Anything/DOC_Machines/Source/Img/a%20b.png", str(md), [str(root)])
    assert found and found.endswith("Img\\a b.png")
    assert resolve_md_link("Q:/Anything/Elsewhere/none.png", str(md), [str(root)]) is None


def test_documentation_server_tools(manual):
    cfg, _ = manual
    from mcp import Client

    from dmx_docs.server import build_server, instructions_for

    async def run():
        async with Client(build_server(cfg)) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    listed = asyncio.run(run())
    tool = listed["search"]
    props = (getattr(tool, "input_schema", None) or tool.inputSchema)["properties"]
    assert "category" in props and "language" in props and "project" not in props
    assert "list_projects" not in listed
    text = instructions_for(cfg)
    assert "language=" in text and "8_Documentation" in text and "(OCR)" in text
    assert cfg.server_name == "dmx-documentation"
