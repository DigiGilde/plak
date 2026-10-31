"""The API contract: SemVer, oasdiff and the schema file (plak_release.api)."""

from __future__ import annotations

import json

import pytest
from release_support import MAIN, ROOT, SPEC, change, fake_oasdiff, spec_with

from plak_release import api, errors


class TestApiVersion:
    def test_the_first_contract_keeps_the_version_the_code_has(self):
        assert api.next_api_version(None, json.loads(spec_with("1.4.2")), []) == "1.4.2"

    def test_an_unchanged_schema_keeps_the_released_version(self):
        """Minor and patch belong to the release: a hand edit in the code
        is not what decides them."""
        base, head = json.loads(spec_with("1.3.0")), json.loads(spec_with("1.9.9"))
        assert api.next_api_version(base, head, []) == "1.3.0"

    def test_a_change_oasdiff_does_not_list_is_a_patch(self):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with(description="Clearer."))
        assert api.next_api_version(base, head, []) == "1.3.3"

    @pytest.mark.parametrize("level", [1, 2], ids=["info", "warn"])
    def test_a_change_oasdiff_lists_as_compatible_is_a_minor(self, level):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with(paths=("/a", "/b")))
        assert api.next_api_version(base, head, [change(level)]) == "1.4.0"

    def test_a_breaking_change_without_a_new_major_is_refused(self):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with(paths=()))
        with pytest.raises(errors.ReleaseError) as raised:
            api.next_api_version(base, head, [change(3, "api path removed", "/a", "POST"), change(1)])
        assert raised.value.problems == [
            "Breaking API change: POST /a: api path removed.",
            "A breaking change needs a new major: serve the API under /-/api/v2 and set API_VERSION in "
            "backend/src/plak/main.py to '2.0.0' (docs/publishing.md).",
        ]

    def test_a_change_without_an_operation_names_its_section(self):
        problems = api.breaking_problems([{"level": 3, "text": "t", "section": "components"}], 1)
        assert problems[0] == "Breaking API change: components: t."

    def test_a_new_major_starts_at_zero_and_may_break(self):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with("2.0.0", paths=()))
        assert api.next_api_version(base, head, [change(3)]) == "2.0.0"
        assert api.next_api_version(base, json.loads(spec_with("2.7.1")), []) == "2.0.0"

    def test_a_major_below_the_released_one_is_refused(self):
        with pytest.raises(errors.ReleaseError, match="has major 1, below the released 2"):
            api.next_api_version(json.loads(spec_with("2.0.0")), json.loads(spec_with("1.0.0")), [])

    @pytest.mark.parametrize("version", ["1.0", "v1.0.0", "01.0.0", "1.0.0-beta", ""])
    def test_a_version_that_is_not_major_minor_patch_is_refused(self, version):
        with pytest.raises(errors.ReleaseError, match=r"is not MAJOR\.MINOR\.PATCH"):
            api.next_api_version(None, json.loads(spec_with(version)), [])
        with pytest.raises(errors.ReleaseError, match=r"backend/openapi\.json at the newest tag"):
            api.next_api_version(json.loads(spec_with(version)), json.loads(SPEC), [])

    def test_only_the_api_version_line_changes(self):
        assert api.set_api_version(MAIN, "1.4.0") == MAIN.replace('"1.0.0"', '"1.4.0"')

    @pytest.mark.parametrize("text", ["app = 1\n", MAIN + MAIN], ids=["none", "twice"])
    def test_a_main_without_exactly_one_api_version_is_refused(self, text):
        with pytest.raises(errors.ReleaseError, match="expected one API_VERSION line"):
            api.set_api_version(text, "1.4.0")

    def test_the_real_api_version_line_can_be_edited(self):
        text = (ROOT / "backend/src/plak/main.py").read_text(encoding="utf-8")
        assert 'API_VERSION = "9.8.7"\n' in api.set_api_version(text, "9.8.7")

    def test_the_release_writes_the_schema_the_way_the_module_prints_it(self):
        """So the committed file and a fresh print only differ where the
        API differs. The backend's printer cannot be imported here (it pulls
        in the app), so its one expression is held to the same text."""
        rendering = 'json.dumps(document, indent=2, ensure_ascii=False) + "\\n"'
        printer = (ROOT / "backend" / "src" / "plak" / "api" / "openapi_file.py").read_text(encoding="utf-8")
        assert f"    return {rendering}\n" in printer
        spec = json.loads(spec_with(description="Plak publiceert één site."))
        assert api.render_spec(spec, "1.0.0") == json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
        assert json.loads(api.render_spec(spec, "1.2.0"))["info"]["version"] == "1.2.0"

    @pytest.mark.parametrize(("text", "reason"), [("{", "is not JSON"), ("[]", "is not an OpenAPI document")])
    def test_a_schema_that_is_not_a_json_object_is_refused(self, text, reason):
        with pytest.raises(errors.ReleaseError, match=f"^The API schema {reason}"):
            api.parse_spec(text, "The API schema")


class TestOasdiff:
    def test_it_compares_the_two_schemas_without_fetching_references(self, tmp_path):
        binary = fake_oasdiff(tmp_path, json.dumps([change(1)]))
        assert api.oasdiff(binary)("base\n", "head\n") == [change(1)]
        log = (tmp_path / "oasdiff.log").read_text(encoding="utf-8").splitlines()
        assert log[0] == "changelog"
        assert log[3:6] == ["--format", "json", "--allow-external-refs=false"]
        assert log[6:] == ["base", "---", "head"]

    def test_no_output_is_no_changes(self, tmp_path):
        assert api.oasdiff(fake_oasdiff(tmp_path, ""))("a", "b") == []

    @pytest.mark.parametrize(
        ("exit_code", "stderr", "reason"),
        [(1, "failed to load base", "oasdiff: failed to load base"), (2, "", "oasdiff: exit 2")],
    )
    def test_a_failure_is_a_refusal(self, tmp_path, exit_code, stderr, reason):
        with pytest.raises(errors.ReleaseError, match=f"^{reason}$"):
            api.oasdiff(fake_oasdiff(tmp_path, "", exit_code, stderr))("a", "b")

    def test_output_that_is_not_json_is_a_refusal(self, tmp_path):
        with pytest.raises(errors.ReleaseError, match="its output is not JSON"):
            api.oasdiff(fake_oasdiff(tmp_path, "API changes:"))("a", "b")
