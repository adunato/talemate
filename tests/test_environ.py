import pytest

from talemate.environ import env_port, env_str

VAR = "TALEMATE_TEST_ENV_VAR"


@pytest.fixture(autouse=True)
def clear_var(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)


def _set(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(VAR, value)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "default"),
        ("", "default"),
        ("   \t  ", "default"),
        ("  hello  ", "hello"),
        ("0.0.0.0", "0.0.0.0"),
    ],
)
def test_env_str(monkeypatch, raw, expected):
    _set(monkeypatch, raw)
    assert env_str(VAR, "default") == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 8082),
        ("", 8082),
        ("   ", 8082),
        ("9090", 9090),
        ("  9090  ", 9090),
        ("1", 1),
        ("65535", 65535),
    ],
)
def test_env_port_valid(monkeypatch, raw, expected):
    _set(monkeypatch, raw)
    assert env_port(VAR, 8082) == expected


@pytest.mark.parametrize(
    "raw,error_fragment",
    [
        ("0", "out of range"),
        ("65536", "out of range"),
        ("-1", "out of range"),
        ("abc", "not a valid port number"),
        ("8082.0", "not a valid port number"),
        ("not-a-port", "not a valid port number"),
    ],
)
def test_env_port_rejected(monkeypatch, capsys, raw, error_fragment):
    _set(monkeypatch, raw)
    with pytest.raises(SystemExit) as exc:
        env_port(VAR, 8082)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert VAR in out
    assert error_fragment in out
    assert raw in out
