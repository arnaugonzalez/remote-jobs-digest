"""./rjs config --check / --show (Fase 5)."""

from __future__ import annotations



from remote_jobs_digest import config_check


def test_check_sin_fichero_falla_y_sugiere_init(tmp_path, capsys):
    ok = config_check.check(str(tmp_path / "no-existe.yaml"))
    assert ok is False
    assert "rjs init" in capsys.readouterr().out


def test_check_fichero_invalido_reporta_rutas_de_campo(tmp_path, capsys):
    f = tmp_path / "c.yaml"
    f.write_text("salary:\n  floor: {EUR: 10000}\n  warn: {EUR: 20000}\n",
                encoding="utf-8")
    ok = config_check.check(str(f))
    assert ok is False
    out = capsys.readouterr().out
    assert "salary.warn.EUR" in out


def test_show_no_falla(capsys):
    config_check.show()
    out = capsys.readouterr().out
    assert "experience:" in out
