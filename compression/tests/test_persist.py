import os

import compress
from compress.persist import persist


def test_writes_and_returns_path(tmp_path):
    path, err = persist(b'{"a":1}', "snyk", str(tmp_path))
    assert err is None and path.endswith(".json")
    assert open(path, "rb").read() == b'{"a":1}'


def test_text_payload_gets_txt_extension(tmp_path):
    path, _ = persist(b"plain log line", "logs", str(tmp_path))
    assert path.endswith(".txt")


def test_read_only_dir_returns_eacces(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        path, err = persist(b"x", "snyk", str(ro))
        assert path is None and err == "EACCES"
    finally:
        os.chmod(ro, 0o700)


def test_env_var_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("LDM_COMPRESS_DIR", str(tmp_path))
    path, err = persist(b"x", "logs", None)
    assert err is None and str(tmp_path) in path


def test_render_survives_an_unwritable_sink(tmp_path):
    ro = tmp_path / "ro2"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        text, meta = compress.render({"vulnerabilities": [{"id": "a", "severity": "low"}]},
                                     preset="snyk", sink=str(ro), store=False)
        assert meta["persisted"] is False
        assert "not persisted (EACCES)" in text
    finally:
        os.chmod(ro, 0o700)
