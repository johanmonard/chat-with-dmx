from datetime import datetime

from dmx_docs import extract


def u(s):
    return s.encode("utf-16-le")


def systime(dt):  # FILETIME: 100 ns since 1601-01-01
    return int((dt - datetime(1601, 1, 1)).total_seconds() * 10**7).to_bytes(8, "little")


def props(*entries):  # top-level __properties_version1.0: 32-byte header, then 16-byte entries
    out = bytes(32)
    for tag, value in entries:
        out += tag.to_bytes(4, "little") + (6).to_bytes(4, "little") + value
    return out


STREAMS = {
    "__substg1.0_0037001F": u("RE: Offer 3001234 - Line 5"),
    "__substg1.0_0C1A001F": u("Jane Doe"),
    "__substg1.0_0E04001F": u("sales@acme.example"),
    "__substg1.0_1000001F": u("Dear John,\r\nplease quote two Paloma 3R.\r\n"),
    "__properties_version1.0": props((0x00390040, systime(datetime(2021, 9, 15, 14, 30)))),
    "__attach_version1.0_#00000000/__substg1.0_3707001F": u("Layout_3001234.pdf"),
}


def test_msg_fields():
    m = extract.msg_fields(STREAMS)
    assert m["subject"] == "RE: Offer 3001234 - Line 5" and m["sender"] == "Jane Doe"
    assert m["to"] == "sales@acme.example"
    assert m["date"] == "2021-09-15 14:30" and "two Paloma 3R" in m["body"]
    assert m["attachments"] == ["Layout_3001234.pdf"]


def test_msg_fields_ansi_and_missing():
    m = extract.msg_fields({"__substg1.0_0037001E": "Offre ACME".encode("cp1252"),
                            "__substg1.0_1000001E": "Prix demandé".encode("cp1252")})
    assert (m["subject"], m["body"], m["date"], m["attachments"]) == ("Offre ACME", "Prix demandé", "", [])
    assert (m["sender"], m["to"]) == ("", "")


def test_msg_fields_delivery_time_fallback_and_short_name_attachment():
    streams = {
        "__properties_version1.0": props((0x0E060040, systime(datetime(2020, 1, 2, 3, 4)))),
        "__attach_version1.0_#00000001/__substg1.0_3704001F": u("short.xls"),
        "__attach_version1.0_#00000000/__substg1.0_3707001F": u("A long name.xlsx"),
        "__attach_version1.0_#00000000/__substg1.0_3704001F": u("ALONGN~1.XLS"),
    }
    m = extract.msg_fields(streams)
    assert m["date"] == "2020-01-02 03:04"
    assert m["attachments"] == ["A long name.xlsx", "short.xls"]


def test_msg_fields_submit_time_wins_over_delivery_time():
    streams = {"__properties_version1.0": props((0x0E060040, systime(datetime(2020, 1, 2, 3, 4))),
                                                (0x00390040, systime(datetime(2019, 5, 6, 7, 8))))}
    assert extract.msg_fields(streams)["date"] == "2019-05-06 07:08"


def test_extract_msg_dispatch(tmp_path, monkeypatch):
    path = tmp_path / "x.msg"
    path.write_bytes(b"not read, _read_msg_streams is patched")
    monkeypatch.setattr(extract, "_read_msg_streams", lambda p: STREAMS)
    r = extract.extract_file(str(path))
    assert r.status == "ok" and len(r.pages) == 1 and r.n_pages == 1
    assert r.title == "RE: Offer 3001234 - Line 5"
    text = r.pages[0][1]
    assert text.startswith("Subject: RE: Offer 3001234 - Line 5\nFrom: Jane Doe")
    assert "\nTo: sales@acme.example\nDate: 2021-09-15 14:30\nAttachments: Layout_3001234.pdf\n\n" in text
    assert text.endswith("please quote two Paloma 3R.")


def test_extract_msg_empty_and_error(tmp_path, monkeypatch):
    path = tmp_path / "x.msg"
    path.write_bytes(b"x")
    monkeypatch.setattr(extract, "_read_msg_streams", lambda p: {})
    assert extract.extract_file(str(path)).status == "empty"

    def boom(p):
        raise OSError("not an OLE file")
    monkeypatch.setattr(extract, "_read_msg_streams", boom)
    r = extract.extract_file(str(path))
    assert r.status == "error" and "OSError" in r.error


def test_attachment_payloads_are_never_read(monkeypatch):
    import sys
    import types

    opened = []

    class FakeOle:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def listdir(self, streams=True, storages=False):
            return [["__substg1.0_0037001F"], ["__attach_version1.0_#00000000", "__substg1.0_3707001F"],
                    ["__attach_version1.0_#00000000", "__substg1.0_3704001F"],
                    ["__attach_version1.0_#00000000", "__substg1.0_37010102"],  # the attached file itself
                    ["__attach_version1.0_#00000000", "__substg1.0_370E001F"],
                    ["__attach_version1.0_#00000000", "__substg1.0_3701000D", "__substg1.0_0037001F"]]  # embedded

        def openstream(self, entry):
            opened.append("/".join(entry))
            return types.SimpleNamespace(read=lambda: u("x"))

    monkeypatch.setitem(sys.modules, "olefile", types.SimpleNamespace(OleFileIO=FakeOle))
    streams = extract._read_msg_streams("x.msg")
    assert sorted(streams) == ["__attach_version1.0_#00000000/__substg1.0_3704001F",
                               "__attach_version1.0_#00000000/__substg1.0_3707001F", "__substg1.0_0037001F"]
    assert not [name for name in opened if "37010102" in name or "370E" in name]
