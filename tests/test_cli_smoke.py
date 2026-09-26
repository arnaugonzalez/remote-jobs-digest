"""End-to-end without network: CLI plumbing, example-profile contract and one
full run() over fake sources."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from remote_jobs_digest import __version__, cli, config_check
from remote_jobs_digest.sources.base import Job

EXAMPLES = sorted((Path(__file__).parent.parent / "src" / "remote_jobs_digest" / "examples").glob("*.yaml"))


def test_help_and_version(capsys):
    cli.main(["--help"])
    assert "rjs init" not in capsys.readouterr().err
    cli.main(["--version"])
    assert capsys.readouterr().out.strip() == __version__


def test_unknown_command_exits():
    with pytest.raises(SystemExit):
        cli.main(["nope"])


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_example_profiles_are_valid(example):
    assert config_check.check(str(example))


def test_init_from_example_then_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RJS_HOME", str(tmp_path))
    cli.main(["init", "--from", str(EXAMPLES[0])])
    assert (tmp_path / "config.yaml").exists()
    cli.main(["config", "check"])
    assert "is valid" in capsys.readouterr().out
    with pytest.raises(SystemExit):   # refuses to overwrite without --force
        cli.main(["init", "--from", str(EXAMPLES[0])])


def test_run_requires_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RJS_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "--no-ai"])
    assert exc.value.code == 2


def test_full_run_over_fake_sources(tmp_path, monkeypatch, capsys):
    from remote_jobs_digest import config, notify, scraper
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(scraper, "SEEN_PATH", str(tmp_path / "seen.json"))
    good = Job(source="remotive", title="Backend Engineer", company="Acme",
               url="https://example.test/1", location="Remote - Spain",
               description="FastAPI, Python, PostgreSQL. 3+ years. Worldwide.")
    bad = Job(source="remoteok", title="Staff Engineer", company="Beta",
              url="https://example.test/2", location="San Francisco, CA",
              description="Java. 10+ years. Must be located in the US.")
    monkeypatch.setitem(scraper.REGISTRY, "fake", lambda: [good, bad])
    sent = []
    monkeypatch.setattr(notify, "send_telegram", sent.append)
    stats = scraper.run(["fake"], send_telegram=True, top_n=5, use_ai=False)
    out = capsys.readouterr().out
    assert stats["apta"] == 1 and stats["descartada"] == 1
    assert "Backend Engineer" in out and "via Remotive" in out
    assert Path(stats["digest_path"]).read_text().startswith("💼")
    assert sent == []   # Telegram not configured -> not called


def test_init_from_bundled_example_by_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RJS_HOME", str(tmp_path))
    cli.main(["init", "--list-examples"])
    assert "frontend-latam" in capsys.readouterr().out
    cli.main(["init", "--from", "frontend-latam"])
    assert "react" in (tmp_path / "config.yaml").read_text()


@pytest.mark.parametrize("cmd", ["run", "boards discover"])
def test_help_works_before_init(tmp_path, cmd):
    # Subprocess: the module must be imported fresh, with no config on disk.
    env = {k: v for k, v in os.environ.items() if not k.startswith("RJS_")}
    env["RJS_HOME"] = str(tmp_path)
    out = subprocess.run([sys.executable, "-m", "remote_jobs_digest", *cmd.split(), "--help"],
                         env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "usage:" in out.stdout


def test_paths_display_contracts_home(monkeypatch, tmp_path):
    from remote_jobs_digest import paths
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.display(tmp_path / "a" / "b.yaml") == "~/a/b.yaml"
    assert paths.display(f"{tmp_path}x/c") == f"{tmp_path}x/c"
    assert paths.display("/etc/rjs") == "/etc/rjs"
