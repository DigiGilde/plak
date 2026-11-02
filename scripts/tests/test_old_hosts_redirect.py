"""deploy/old-hosts-haproxy.cfg: what has to hold for the redirect on the old hosts."""

from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "deploy" / "old-hosts-haproxy.cfg"


def _lines() -> list[str]:
    return [line.strip() for line in CONFIG.read_text().splitlines()]


def test_the_log_line_leaves_the_query_out():
    """The key of a secret link sits in the query."""
    log_format = next(line for line in _lines() if line.startswith("log-format"))
    assert "%HPO" in log_format
    for with_the_query in ("%r", "%HU", "%HQ"):
        assert with_the_query not in log_format
    assert not any(line.split()[:2] == ["option", "httplog"] for line in _lines())


def test_a_write_is_refused_before_anything_redirects():
    lines = _lines()
    refusal = next(number for number, line in enumerate(lines) if "return status 410" in line)
    redirects = [number for number, line in enumerate(lines) if line.startswith("http-request redirect")]
    assert len(redirects) == 2
    assert refusal < min(redirects)


def test_each_old_host_goes_to_its_own_new_host():
    lines = _lines()
    assert "acl from_admin hdr(host),field(1,:) -i beheer.plak.rijks.app" in lines
    assert 'http-request redirect prefix "https://beheer.toon.rijks.app" code 302 if from_admin' in lines
    assert 'http-request redirect prefix "https://toon.rijks.app" code 302' in lines


def test_the_probes_are_answered_and_stay_out_of_the_log():
    lines = _lines()
    assert "http-request set-log-level silent if is_health" in lines
    assert 'http-request return status 200 content-type "text/plain" string "ok" if is_health' in lines
