from dmx_docs.extract import TXT_MAX_BYTES, extract_file


def test_txt_encodings(tmp_path):
    for name, data in {"utf8.txt": "Réglage du préhenseur".encode("utf-8"),
                       "bom.txt": "\ufeffRéglage du préhenseur".encode("utf-8"),
                       "utf16.txt": "Réglage du préhenseur".encode("utf-16"),
                       "cp1252.txt": "Réglage du préhenseur".encode("cp1252")}.items():
        p = tmp_path / name
        p.write_bytes(data)
        r = extract_file(str(p))
        assert (r.status, r.n_pages, r.pages) == ("ok", 1, [(1, "Réglage du préhenseur")]), name


def test_txt_is_cut_at_the_limit(tmp_path):
    p = tmp_path / "big.txt"
    p.write_bytes(b"ligne\n" * (TXT_MAX_BYTES // 6 + 1000))
    text = extract_file(str(p)).pages[0][1]
    assert len(text) < TXT_MAX_BYTES + 100 and text.endswith("[fichier coupé après 2 Mo]")


def test_txt_that_is_not_text(tmp_path):                       # Review Focus 5
    blank = tmp_path / "blank.txt"
    blank.write_bytes(b" \r\n\t")
    assert extract_file(str(blank)).status == "empty"
    binary = tmp_path / "binary.txt"
    binary.write_bytes(bytes(range(256)) * 40)
    assert extract_file(str(binary)).status in ("ok", "empty")   # no exception, no 'error'
