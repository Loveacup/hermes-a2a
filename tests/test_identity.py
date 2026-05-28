"""Tests for core/identity.py — identity_prefix loader."""


def test_env_var_wins(hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_A2A_IDENTITY", "【系统提示】 from env")
    import identity
    out = identity.load_identity_prefix(str(hermes_home), "regent")
    assert out.startswith("【系统提示】 from env")
    assert out.endswith("\n\n")


def test_profile_file_used(hermes_home, monkeypatch):
    monkeypatch.delenv("HERMES_A2A_IDENTITY", raising=False)
    p = hermes_home / "profiles" / "regent"
    p.mkdir(parents=True)
    (p / "a2a-identity.md").write_text("【系统提示】profile-specific")
    import identity
    out = identity.load_identity_prefix(str(hermes_home), "regent")
    assert "profile-specific" in out
    assert out.endswith("\n\n")


def test_home_default_file(hermes_home, monkeypatch):
    monkeypatch.delenv("HERMES_A2A_IDENTITY", raising=False)
    (hermes_home / "a2a-identity.md").write_text("【系统提示】shared default")
    import identity
    out = identity.load_identity_prefix(str(hermes_home), "anything")
    assert "shared default" in out


def test_generic_fallback(hermes_home, monkeypatch):
    monkeypatch.delenv("HERMES_A2A_IDENTITY", raising=False)
    import identity
    out = identity.load_identity_prefix(str(hermes_home), "myprof")
    assert "【系统提示】" in out
    assert "myprof" in out


def test_no_business_strings_in_core_default(hermes_home, monkeypatch):
    """Generic fallback must not leak business names."""
    monkeypatch.delenv("HERMES_A2A_IDENTITY", raising=False)
    import identity
    out = identity.load_identity_prefix(str(hermes_home), "anything")
    for banned in ("三省六部", "监国太子", "小黄", "Alex"):
        assert banned not in out
