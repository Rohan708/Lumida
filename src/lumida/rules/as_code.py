"""Rules as code (Stage 4.6): approved rules in a YAML file a team keeps in git.

- ``export_rules``: the source's active and disabled rules, in a stable order, so exporting
  twice gives the same file and a change shows up as a small diff in review.
- ``plan_import`` / ``apply_import``: what loading a file would change (new rules, rules
  switched on or off), shown first and applied only when asked. Rules missing from the
  file are left alone unless ``prune`` is set (then they're disabled, never deleted).
- ``check_file``: run a file's active rules against any database, without Lumida's own
  store: the structure is read live. For a customer's CI (``lumida check``): bad data in a
  staging database fails the build. Read-only, counts only, like the nightly run.

The file holds rule definitions only, never row values or connection details.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from lumida.discovery.catalog import Catalog, ColumnInfo, TableInfo
from lumida.graph.models import DataSource, Rule, RuleStatus
from lumida.ingestion.base import DiscoveredTable, SourceAdapter
from lumida.rules.spec import RuleSpec, describe, parse_spec, signature, spec_json, validate
from lumida.rules.store import add_user_rule, review_rule, rules_of

FILE_VERSION = 1
EXPORTED = (RuleStatus.ACTIVE, RuleStatus.DISABLED)  # decisions; proposals stay in the app


class RulesFileError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileRule(_Strict):
    rule: str | None = None  # the readable form; informative, rewritten on export
    status: Literal["active", "disabled"] = "active"
    origin: str | None = None  # informative
    approved_by: str | None = None  # informative
    spec: dict[str, Any]


class RulesFile(_Strict):
    version: int = Field(FILE_VERSION, ge=1, le=FILE_VERSION)
    source: str | None = None  # informative: which database it was exported from
    rules: list[FileRule] = Field(default_factory=list)


HEADER = """\
# Lumida rules{for_source}. Each rule is checked as read-only SQL; only counts come back.
# Review changes like code, then load them with:
#   lumida rules import <source> lumida-rules.yaml           (shows what would change)
#   lumida rules import <source> lumida-rules.yaml --apply
# Check a database against this file in CI (exit code 1 when a rule is broken):
#   lumida check --rules lumida-rules.yaml --connection-ref STAGING_DB_URL
"""


def export_rules(session: Session, source: DataSource) -> str:
    ordered: list[tuple[tuple[str, str], dict[str, Any]]] = []
    for rule in rules_of(session, source):
        if rule.status not in EXPORTED:
            continue
        spec = parse_spec(rule.definition["spec"])
        entry = {
            "rule": describe(spec),
            "status": rule.status.value,
            "origin": rule.origin.value,
            "approved_by": rule.reviewed_by or rule.created_by,
            "spec": spec_json(spec),
        }
        ordered.append(((describe(spec), signature(spec)), entry))
    entries = [entry for _, entry in sorted(ordered, key=lambda pair: pair[0])]
    body = yaml.safe_dump(
        {"version": FILE_VERSION, "source": source.name, "rules": entries},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    return HEADER.format(for_source=f" for {source.name}") + body


def parse_rules_file(text: str) -> tuple[RulesFile, list[tuple[RuleSpec, str]]]:
    """The file and each rule's parsed spec with its wanted status. Malformed files raise
    ``RulesFileError`` naming the rule; nothing is half-loaded."""
    try:
        data = yaml.safe_load(text) or {}
        parsed = RulesFile.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise RulesFileError(f"not a Lumida rules file: {_short(exc)}") from exc
    specs: list[tuple[RuleSpec, str]] = []
    seen: set[str] = set()
    for i, entry in enumerate(parsed.rules, start=1):
        try:
            spec = parse_spec(entry.spec)
        except ValidationError as exc:
            raise RulesFileError(f"rule {i} ({entry.rule or 'unnamed'}): {_short(exc)}") from exc
        sig = signature(spec)
        if sig in seen:
            raise RulesFileError(f"rule {i} ({describe(spec)}) is in the file twice")
        seen.add(sig)
        specs.append((spec, entry.status))
    return parsed, specs


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors(include_url=False, include_input=False)[0]
        where = ".".join(str(p) for p in first["loc"])
        return f"{where}: {first['msg']}" if where else first["msg"]
    return str(exc).splitlines()[0]


# --- importing -----------------------------------------------------------------------------


@dataclass
class ImportPlan:
    add: list[tuple[RuleSpec, str]] = field(default_factory=list)  # (spec, status)
    enable: list[Rule] = field(default_factory=list)  # disabled/proposed/rejected -> active
    disable: list[Rule] = field(default_factory=list)  # active -> disabled
    unchanged: int = 0
    not_in_file: list[Rule] = field(default_factory=list)  # active here, absent from the file
    invalid: list[str] = field(default_factory=list)  # can't run on this source

    @property
    def changes(self) -> int:
        return len(self.add) + len(self.enable) + len(self.disable)

    def lines(self, prune: bool = False) -> list[str]:
        out = [f"+ add ({status}): {describe(spec)}" for spec, status in self.add]
        out += [f"~ switch on: {r.name}" for r in self.enable]
        out += [f"~ switch off: {r.name}" for r in self.disable]
        verb = "- switch off (not in the file)" if prune else "  kept (not in the file)"
        out += [f"{verb}: {r.name}" for r in self.not_in_file]
        out += [f"! can't use: {problem}" for problem in self.invalid]
        return out


def plan_import(
    session: Session, source: DataSource, catalog: Catalog, wanted: list[tuple[RuleSpec, str]]
) -> ImportPlan:
    plan = ImportPlan()
    existing = {r.definition.get("signature"): r for r in rules_of(session, source)}
    in_file = set()
    for spec, status in wanted:
        sig = signature(spec)
        in_file.add(sig)
        rule = existing.get(sig)
        if rule is None:
            problems = validate(spec, catalog)
            if problems:
                plan.invalid.append(f"{describe(spec)}: {'; '.join(problems)}")
            else:
                plan.add.append((spec, status))
        elif status == "active" and rule.status is not RuleStatus.ACTIVE:
            plan.enable.append(rule)
        elif status == "disabled" and rule.status is RuleStatus.ACTIVE:
            plan.disable.append(rule)
        else:
            plan.unchanged += 1
    plan.not_in_file = [
        r for sig, r in existing.items() if sig not in in_file and r.status is RuleStatus.ACTIVE
    ]
    return plan


def apply_import(
    session: Session,
    source: DataSource,
    catalog: Catalog,
    plan: ImportPlan,
    *,
    by: str,
    now: datetime,
    prune: bool = False,
) -> None:
    """Apply a plan. Every change goes through the same functions as a review in the app,
    so it's validated and recorded in each rule's history."""
    note = "from a rules file"
    for spec, status in plan.add:
        rule = add_user_rule(session, source, catalog, spec, by, now)
        if status == "disabled":
            review_rule(session, rule, RuleStatus.DISABLED, actor=by, now=now, note=note)
    for rule in plan.enable:
        if rule.status is RuleStatus.REJECTED:  # rejected -> proposed -> active
            review_rule(session, rule, RuleStatus.PROPOSED, actor=by, now=now, note=note)
        review_rule(session, rule, RuleStatus.ACTIVE, actor=by, now=now, note=note)
    for rule in plan.disable + (plan.not_in_file if prune else []):
        review_rule(session, rule, RuleStatus.DISABLED, actor=by, now=now, note=note)


# --- checking in CI ------------------------------------------------------------------------


def catalog_from_tables(source: DataSource, tables: list[DiscoveredTable]) -> Catalog:
    """A catalog read live from the database (no history): enough to check rules."""
    catalog = Catalog(source=source)
    for t in tables:
        o = t.observation
        columns = {
            c.name: ColumnInfo(
                uuid.uuid4(), c.name, c.type_family, c.native_type, is_nullable=c.is_nullable
            )
            for c in o.columns
        }
        unique = tuple(tuple(u.get("columns", ())) for u in o.unique_constraints)
        catalog.tables[t.ref] = TableInfo(
            asset_key=uuid.uuid4(),
            schema_name=o.schema_name,
            name=o.name,
            columns=columns,
            primary_key=o.primary_key,
            unique_keys=unique,
            historical_keys=(),
            foreign_keys=o.foreign_keys,
            estimated_rows=t.estimated_rows,
            kind=o.kind.value,
        )
    return catalog


@dataclass(frozen=True)
class CheckOutcome:
    rule: str
    ok: bool
    checked_rows: int = 0
    violating_rows: int = 0
    sampled: bool = False
    sample: list[dict[str, str]] = field(default_factory=list)  # primary keys only
    problem: str | None = None  # why it couldn't run


def check_file(
    adapter: SourceAdapter,
    catalog: Catalog,
    wanted: list[tuple[RuleSpec, str]],
    sample_size: int,
) -> list[CheckOutcome]:
    outcomes = []
    for spec, status in wanted:
        if status != "active":
            continue
        text = describe(spec)
        problems = validate(spec, catalog)
        if problems:
            outcomes.append(CheckOutcome(text, False, problem="; ".join(problems)))
            continue
        table = catalog.tables[spec.table]
        try:
            result = adapter.check_rule(spec, table.primary_key, table.estimated_rows, sample_size)
        except Exception as exc:  # one rule that can't run doesn't hide the others
            reason = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
            outcomes.append(CheckOutcome(text, False, problem=reason[:300]))
            continue
        outcomes.append(
            CheckOutcome(
                text,
                result.violating_rows == 0,
                result.checked_rows,
                result.violating_rows,
                result.sampled,
                result.sample,
            )
        )
    return outcomes
