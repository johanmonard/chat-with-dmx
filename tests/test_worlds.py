import pytest

from dmx_docs.config import load_config

WORLDS = r"""
[index]
data_dir = 'DATA'
extensions = ['.pdf', '.docx', '.doc']

[worlds.projects]
title = "Projects"
profile = "projects"

[worlds.marketing]
title = "Marketing"
profile = "marketing"
extensions = ['.pdf', '.pptx', '.ppt']
roots = ['\\server\share\Marketing']
"""


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    p = tmp_path / "config.toml"
    p.write_text(WORLDS.replace("DATA", (tmp_path / "data").as_posix()), encoding="utf-8")
    return p


def test_default_world_is_projects(config_file, tmp_path):
    # Claude Desktop's existing entry has no --world: it must keep serving the projects.
    cfg = load_config(config_file)
    data = tmp_path / "data"
    assert (cfg.world, cfg.world_title, cfg.profile) == ("projects", "Projects", "projects")
    assert cfg.db_path == data / "projects" / "index.sqlite3"
    assert cfg.models_dir == data / "models"  # one embedding model for all worlds
    assert cfg.register_path == str(data / "projects" / "register.csv")
    assert cfg.extensions == [".pdf", ".docx", ".doc"]
    assert cfg.server_name == "dmx-docs"


def test_marketing_world_has_its_own_folder_and_file_types(config_file, tmp_path):
    cfg = load_config(config_file, world="marketing")
    data = tmp_path / "data"
    assert (cfg.world, cfg.world_title, cfg.profile) == ("marketing", "Marketing", "marketing")
    assert cfg.db_path == data / "marketing" / "index.sqlite3"
    assert cfg.logs_dir == data / "marketing" / "logs"
    assert cfg.extensions == [".pdf", ".pptx", ".ppt"]
    assert cfg.roots == [r"\\server\share\Marketing"]
    assert cfg.register_path is None
    assert cfg.server_name == "dmx-marketing"


def test_world_from_environment(config_file, monkeypatch):
    monkeypatch.setenv("DMX_DOCS_WORLD", "marketing")
    assert load_config(config_file).world == "marketing"
    assert load_config(config_file, world="projects").world == "projects"  # the option wins


def test_unknown_world_lists_the_configured_ones(config_file):
    with pytest.raises(ValueError, match="Configured worlds .*: projects, marketing"):
        load_config(config_file, world="documentation")


def test_single_index_config_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    p = tmp_path / "config.toml"
    p.write_text(f"[index]\ndata_dir = '{(tmp_path / 'data').as_posix()}'\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.world is None and cfg.profile == "projects"
    assert cfg.db_path == tmp_path / "data" / "index.sqlite3"
    assert cfg.register_path == str(tmp_path / "register.csv")
    with pytest.raises(ValueError, match=r"no \[worlds"):
        load_config(p, world="marketing")


def test_cli_refuses_an_unknown_world(config_file, capsys):
    from dmx_docs.cli import main
    with pytest.raises(SystemExit):
        main(["--config", str(config_file), "--world", "nope", "status"])
    assert "Unknown world 'nope'" in capsys.readouterr().err


def test_vector_cache_lives_in_the_world_folder(config_file):
    from dmx_docs.search import VectorIndex
    cfg = load_config(config_file, world="marketing")
    ids, mat = VectorIndex(cfg)._cache_paths("abc")
    assert ids.parent == mat.parent == cfg.world_dir
