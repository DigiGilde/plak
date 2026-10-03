"""Shape checks on `deploy/plak-project.example.yaml`, the ZAD project file.

The ZAD schema (`opi/schemas/project_v2.json`, RijksICTGilde/RIG-Cluster) does
not live in this repo, so what is pinned here is a reading of that schema as
last checked: it does not branch on the file's own `schema-version`, it just
validates the whole document against one current schema. What matters for us:

* a service entry is a plain string, or a single-key object keyed by the
  service name itself (`- keycloak:\\n    config: {...}`); a second key such
  as a stray `name` or `reference` alongside `config` is rejected outright;
* the domain approval block `domains` is a project-level property, not nested
  under a service;
* the web address fields (`domain-format`, `domain-mode`, `subdomain`,
  `base-domain`, `issuer`, `root-component`) are direct properties of the
  deployment object, not nested under a service config;
* `domain-mode` still exists in the schema and is not derived from
  `domain-format`: the Operations Manager reads it literally and gates the
  root ingress (the bare `{subdomain}.{domain}` host) on it being exactly
  `nice-url`;
* a component has no `probe`;
* `backup.enabled` in the root only sets the PVC label; scheduling happens per
  deployment with an RRULE.

When ZAD's own `opi/schemas/project_v2.json` is present on disk (a sibling
checkout of RijksICTGilde/RIG-Cluster), `test_validates_against_zad_schema`
validates the file against it directly instead of relying on this reading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_FILE = Path(__file__).resolve().parents[2] / "deploy" / "plak-project.example.yaml"
PROJECT: dict[str, Any] = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))

#: A checkout of RijksICTGilde/RIG-Cluster next to this repo, if there is one.
ZAD_SCHEMA_FILE = (
    Path.home()
    / "Projects"
    / "zad"
    / "zad-platform"
    / "operations-manager"
    / "python"
    / "opi"
    / "schemas"
    / "project_v2.json"
)

#: The domain formats that yield a root address alongside the component address:
#: dot-separated and with a {component} prefix that may be left off.
ROOT_ADDRESS_FORMATS = frozenset(
    {"component.deployment.project", "component.deployment.subdomain", "component.subdomain"}
)


def _service_config(services: list[Any] | None, name: str) -> Any:
    """The config of service `name` from a services list, or None."""
    for item in services or []:
        if isinstance(item, dict) and name in item:
            entry = item[name]
            return entry.get("config") if isinstance(entry, dict) else None
    return None


def test_declares_the_operations_manager_latest_schema_version() -> None:
    """Not version dispatch: `schema-version` only drives migration on write.
    Pinned here so the file does not drift from what the Operations Manager
    calls LATEST_SCHEMA_VERSION and writes back after every migration."""
    assert PROJECT["schema-version"] == 2.2


def test_service_entries_are_a_string_or_a_single_key_object() -> None:
    """`service-entry` in project_v2.json is `oneOf: string | object with
    exactly one property`; a stray second key (e.g. `name` next to `config`)
    matches neither branch and is rejected."""
    for services in (PROJECT["services"], *(c["services"] for c in PROJECT["components"])):
        for item in services:
            if isinstance(item, dict):
                assert len(item) == 1, f"service entry has more than one key: {item!r}"


def test_approval_block_is_at_the_project_root() -> None:
    assert "domains" in PROJECT
    assert "domains" not in (_service_config(PROJECT["services"], "publish-on-web") or {})


def test_requested_domain_and_subdomain_belong_on_the_deployment() -> None:
    domains = PROJECT["domains"]
    deployment = PROJECT["deployments"][0]

    assert [d["domain"] for d in domains["allowed-domains"]] == [deployment["base-domain"]]
    subdomains = [s["name"] for d in domains["allowed-subdomains"] for s in d["subdomains"]]
    assert deployment["subdomain"] in subdomains


def test_no_probe_on_a_component() -> None:
    for component in PROJECT["components"]:
        assert "probe" not in component


def test_url_fields_sit_directly_on_the_deployment() -> None:
    for deployment in PROJECT["deployments"]:
        assert "services" not in deployment, "domain fields belong on the deployment, not under a service"
        assert deployment["domain-format"]
        assert deployment["subdomain"]
        assert deployment["base-domain"]
        assert deployment["root-component"]


def test_root_address_needs_domain_mode_nice_url_too() -> None:
    """The root address (plak.<domain>) needs domain-format, subdomain,
    base-domain and root-component all four right, AND domain-mode:
    nice-url: the Operations Manager reads domain-mode literally and does not
    derive it from domain-format. Drop one of the five and there is silently
    no root address, and then the PLAK_*_BASE_URLs point at a host that never
    exists."""
    for deployment in PROJECT["deployments"]:
        assert deployment["domain-format"] in ROOT_ADDRESS_FORMATS
        assert deployment["domain-mode"] == "nice-url"
        components = [c["reference"] for c in deployment["components"]]
        assert deployment["root-component"] in components


def test_backup_becomes_per_deployment_scheduled() -> None:
    """`backup.enabled` in the root only sets the label that makes the PVC
    eligible; without a schedule on the deployment the platform schedules
    nothing and only the manual backup exists."""
    assert PROJECT["backup"]["enabled"] is True
    for deployment in PROJECT["deployments"]:
        schedule = deployment["backup"]["schedule"]
        assert schedule.startswith("FREQ=")
        assert "pvc" in deployment["backup"]["resource_types"]


def test_the_site_quota_fits_the_volume_zad_caps_at_1gi() -> None:
    deployment = PROJECT["deployments"][0]
    env = deployment["components"][0]["env-vars"]
    assert env["PLAK_SITE_MAX_BYTES"] == str(200 * 1024 * 1024)


def test_urls_come_up_with_the_composed_hosts() -> None:
    """The app derives host separation, HSTS and every shareable URL from
    these two, so they must be literally the hosts the domain format
    produces."""
    deployment = PROJECT["deployments"][0]
    admin_host = f"{deployment['root-component']}.{deployment['subdomain']}.{deployment['base-domain']}"
    content_host_name = f"{deployment['subdomain']}.{deployment['base-domain']}"

    env = deployment["components"][0]["env-vars"]
    assert env["PLAK_BASE_URL"] == f"https://{admin_host}"
    assert env["PLAK_CONTENT_BASE_URL"] == f"https://{content_host_name}"

    keycloak = _service_config(PROJECT["services"], "keycloak")
    assert f"https://{content_host_name}/-/oauth2/callback" in keycloak["additional_redirect_uris"]


@pytest.mark.skipif(
    not ZAD_SCHEMA_FILE.exists(), reason="no local RIG-Cluster checkout with opi/schemas/project_v2.json"
)
def test_validates_against_zad_schema() -> None:
    import json

    from jsonschema import Draft202012Validator

    schema = json.loads(ZAD_SCHEMA_FILE.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    errors = sorted(validator.iter_errors(PROJECT), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in errors
    )
