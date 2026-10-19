"""Stage 4.6 against the test lab: rules exported to YAML and imported back (the same
file again, edits shown before they're applied), and `lumida check` running a rules file
against a database without Lumida's own store, as a customer's CI would."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from lumida.cli import main
from lumida.db import create_db_engine
from lumida.discovery.catalog import load_catalog
from lumida.graph.models import DataSource, RuleStatus, SourceKind, utcnow
from lumida.ingestion.postgres import PostgresAdapter
from lumida.ingestion.scan import scan_source
from lumida.ingestion.settings import ScanSettings
from lumida.ingestion.sources import add_source, get_source
from lumida.lab.runner import build_lab
from lumida.rules.as_code import (
    RulesFileError,
    apply_import,
    export_rules,
    parse_rules_file,
    plan_import,
)
from lumida.rules.spec import parse_spec
from lumida.rules.store import add_user_rule, rules_of
from lumida.service.migrate import upgrade_to_head

HOLDS = {"kind": "not_null", "table": "shop.customers", "column": "id"}
QTY_POSITIVE = {"kind": "compare_constant", "table": "legacy.INV_LINE", "column": "QTY",
                "op": ">", "value": 0}  # fmt: skip
BROKEN = {"kind": "compare_constant", "table": "legacy.INV_LINE", "column": "QTY",
          "op": ">", "value": 999_999}  # fmt: skip
LAB_REF = "RULES_AS_CODE_LAB_URL"


@pytest.fixture(scope="module")
def lab(make_database: Callable[[], str]) -> Iterator[tuple[Engine, str]]:
    """A store with the lab scanned as ``shop`` and two approved rules."""
    store_url, lab_url = make_database(), make_database()
    upgrade_to_head(store_url)
    engine = create_db_engine(lab_url)
    build_lab(engine, seed=23, size="tiny")
    engine.dispose()
    store = create_db_engine(store_url)
    with Session(store) as session:
        source = add_source(session, name="shop", kind=SourceKind.POSTGRES, connection_ref=LAB_REF,
                            settings={"exclude_schemas": ["aide_lab"]})  # fmt: skip

        def factory(_s: DataSource) -> Any:
            return PostgresAdapter(lab_url, ScanSettings(exclude_schemas=("aide_lab",)))

        scan_source(session, source, adapter_factory=factory)
        catalog = load_catalog(session, source)
        for spec in (HOLDS, QTY_POSITIVE):
            add_user_rule(session, source, catalog, parse_spec(spec), "maria", utcnow())
        session.commit()
    yield store, lab_url
    store.dispose()


def test_export_is_stable_and_imports_back_unchanged(lab: tuple[Engine, str]) -> None:
    store, _ = lab
    with Session(store) as session:
        source = get_source(session, "shop")
        text = export_rules(session, source)
        assert export_rules(session, source) == text  # same rules, same file
        data = yaml.safe_load(text)
        assert data["version"] == 1
        assert data["source"] == "shop"
        assert [r["rule"] for r in data["rules"]] == [
            "legacy.INV_LINE.QTY > 0", "shop.customers.id is always filled",
        ]  # fmt: skip
        assert {r["approved_by"] for r in data["rules"]} == {"maria"}
        assert "postgresql" not in text  # rule definitions only, no connection details

        _, wanted = parse_rules_file(text)
        plan = plan_import(session, source, load_catalog(session, source), wanted)
        assert (plan.changes, plan.unchanged, plan.not_in_file, plan.invalid) == (0, 2, [], [])


def test_an_edited_file_shows_its_changes_then_applies_them(lab: tuple[Engine, str]) -> None:
    store, _ = lab
    with Session(store) as session:
        source = get_source(session, "shop")
        data = yaml.safe_load(export_rules(session, source))
        qty, not_null = data["rules"]
        not_null["status"] = "disabled"  # switch one off
        data["rules"] = [not_null, {"spec": BROKEN}]  # drop QTY > 0, add a new one
        data["rules"].append({"spec": {"kind": "not_null", "table": "shop.nowhere", "column": "x"}})
        _, wanted = parse_rules_file(yaml.safe_dump(data))
        catalog = load_catalog(session, source)
        plan = plan_import(session, source, catalog, wanted)

        assert [s for s, _ in plan.add] == [parse_spec(BROKEN)]
        assert [r.name for r in plan.disable] == ["shop.customers.id is always filled"]
        assert [r.name for r in plan.not_in_file] == [qty["rule"]]
        assert len(plan.invalid) == 1
        assert "shop.nowhere" in plan.invalid[0]
        assert plan.lines(prune=True)[-2].startswith("- switch off (not in the file)")
        assert {r.status for r in rules_of(session, source)} == {RuleStatus.ACTIVE}  # nothing yet

        apply_import(session, source, catalog, plan, by="ci", now=utcnow(), prune=True)
        session.commit()
        status = {r.name: r.status for r in rules_of(session, source)}
        assert status == {
            "shop.customers.id is always filled": RuleStatus.DISABLED,
            "legacy.INV_LINE.QTY > 0": RuleStatus.DISABLED,  # pruned: switched off, not deleted
            "legacy.INV_LINE.QTY > 999999": RuleStatus.ACTIVE,
        }
        added = next(r for r in rules_of(session, source) if "999999" in r.name)
        assert added.created_by == "ci"
        history = next(r for r in rules_of(session, source) if "QTY > 0" in r.name).evidence
        assert history["review_history"][-1]["note"] == "from a rules file"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("rules: [", "not a Lumida rules file"),
        ("version: 9\nrules: []", "not a Lumida rules file"),
        ("rules:\n  - spec: {kind: nonsense}", "rule 1"),
        (
            "rules:\n  - spec: {kind: unique, table: a.b, column: c}\n"
            "  - spec: {kind: unique, table: a.b, column: c}",
            "twice",
        ),
        (
            "rules:\n  - spec: {kind: unique, table: a.b, column: c}\n    colour: red",
            "not a Lumida",
        ),
    ],
)
def test_malformed_files_are_refused(text: str, message: str) -> None:
    with pytest.raises(RulesFileError, match=message):
        parse_rules_file(text)


def _rules_file(tmp_path: Path, *specs: dict[str, Any]) -> Path:
    path = tmp_path / "lumida-rules.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "rules": [{"spec": s} for s in specs]}),
                    encoding="utf-8")  # fmt: skip
    return path


def test_lumida_check_for_ci(
    lab: tuple[Engine, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, lab_url = lab
    monkeypatch.setenv(LAB_REF, lab_url)
    base = ["check", "--connection-ref", LAB_REF, "--exclude-schemas", "aide_lab"]

    good = _rules_file(tmp_path, HOLDS, QTY_POSITIVE)
    assert main([*base, "--rules", str(good)]) == 0
    out = capsys.readouterr().out
    assert "2 of 2 rules hold" in out
    assert "ok      shop.customers.id is always filled" in out

    bad = _rules_file(tmp_path, HOLDS, BROKEN)
    assert main([*base, "--rules", str(bad), "--json"]) == 1  # fails the build
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    broken = next(r for r in report["rules"] if not r["ok"])
    assert broken["rule"] == "legacy.INV_LINE.QTY > 999999"
    assert broken["violating_rows"] == broken["checked_rows"] > 0
    assert set(broken["sample"][0]) == {"INV_NO", "LINE_NO"}  # primary keys, not row values

    missing = _rules_file(tmp_path, {"kind": "not_null", "table": "shop.gone", "column": "x"})
    assert main([*base, "--rules", str(missing)]) == 1
    assert "ERROR" in capsys.readouterr().out
