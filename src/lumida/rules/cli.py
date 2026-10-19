"""``lumida rules ...`` (per source), ``lumida rule ...`` (one rule) and ``lumida check``
(a rules file against any database, for CI) commands."""

import argparse
import getpass
import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.orm import Session

from lumida.config import get_settings
from lumida.discovery.catalog import load_catalog
from lumida.graph.models import DataSource, Rule, RuleStatus, SourceKind, utcnow
from lumida.ingestion.cli import store_session
from lumida.ingestion.scan import open_adapter
from lumida.ingestion.settings import ScanSettings
from lumida.ingestion.sources import get_source
from lumida.rules.as_code import (
    apply_import,
    catalog_from_tables,
    check_file,
    export_rules,
    parse_rules_file,
    plan_import,
)
from lumida.rules.checks import check_rules
from lumida.rules.settings import DEFAULT_RULE_SETTINGS
from lumida.rules.spec import parse_spec
from lumida.rules.store import add_user_rule, find_rule, review_rule, rules_of

_ACTIONS = {
    "approve": RuleStatus.ACTIVE,
    "reject": RuleStatus.REJECTED,
    "disable": RuleStatus.DISABLED,
    "enable": RuleStatus.ACTIVE,
    "reconsider": RuleStatus.PROPOSED,
}


def add_rule_parsers(subcommands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    rules = subcommands.add_parser("rules", help="business rules of a source")
    commands = rules.add_subparsers(dest="rules_command", required=True)
    p = commands.add_parser("propose", help="ask the AI to propose rules (needs LUMIDA_LLM_*)")
    p.add_argument("name", help="data source name")
    p = commands.add_parser("list", help="rules with their status and id")
    p.add_argument("name")
    p.add_argument("--status", choices=[s.value for s in RuleStatus])
    p = commands.add_parser("check", help="run the active rules now")
    p.add_argument("name")
    p = commands.add_parser("add", help="add your own rule (JSON, see docs/design/ai_rules.md)")
    p.add_argument("name")
    p.add_argument("rule_json", help='e.g. {"kind": "compare_constant", "table": ...}')
    p.add_argument("--by", help="author (default: OS user)")
    p = commands.add_parser("export", help="approved rules as YAML, to keep in git (Stage 4.6)")
    p.add_argument("name")
    p.add_argument("-o", "--output", type=Path, help="file to write (default: print)")
    p = commands.add_parser(
        "import", help="load a rules file: shows the changes; --apply does them"
    )
    p.add_argument("name")
    p.add_argument("file", type=Path)
    p.add_argument("--apply", action="store_true", help="make the changes (default: only show)")
    p.add_argument(
        "--prune", action="store_true", help="switch off active rules missing from the file"
    )
    p.add_argument("--by", help="who (default: OS user)")

    check = subcommands.add_parser(
        "check", help="check a database against a rules file, for CI (exit 1 if a rule is broken)"
    )
    check.add_argument(
        "--rules", type=Path, required=True, help="a file from `lumida rules export`"
    )
    check.add_argument("--connection-ref", required=True,
                       help="NAME of the env var holding the database URL")  # fmt: skip
    check.add_argument("--kind", choices=[k.value for k in SourceKind], default="postgres")
    check.add_argument("--include-schemas", default="", help="comma-separated; default: all")
    check.add_argument("--exclude-schemas", default="", help="comma-separated")
    check.add_argument("--json", action="store_true", help="machine-readable output")

    rule = subcommands.add_parser("rule", help="show or review one rule")
    commands = rule.add_subparsers(dest="rule_command", required=True)
    p = commands.add_parser("show", help="definition, rationale and review history")
    p.add_argument("id", help="rule id (or a unique prefix)")
    for action, status in _ACTIONS.items():
        p = commands.add_parser(action, help=f"make one or more rules {status.value}")
        p.add_argument("ids", nargs="+", metavar="id", help="rule ids (or unique prefixes)")
        p.add_argument("--note", help="why (kept in the rule's history)")
        p.add_argument("--by", help="reviewer name (default: OS user)")


def run_rules_command(args: argparse.Namespace) -> int:
    with store_session() as session:
        source = get_source(session, args.name)
        if args.rules_command == "propose":
            # Imported here: only this command needs an LLM.
            from lumida.reasoning.llm import llm_from_settings
            from lumida.reasoning.propose import propose_rules

            result = propose_rules(session, source, llm_from_settings(get_settings()))
            print(result.summary())
            for r in result.proposed:
                print(f"  {_line(r)}")
            for problem in result.invalid:
                print(f"  invalid: {problem}")
            if result.proposed:
                print("review: lumida rule show ID, then lumida rule approve|reject ID")
        elif args.rules_command == "list":
            status = RuleStatus(args.status) if args.status else None
            found = rules_of(session, source, status)
            for r in found:
                print(_line(r))
            print(f"{len(found)} rules")
        elif args.rules_command == "export":
            text = export_rules(session, source)
            if args.output:
                args.output.write_text(text, encoding="utf-8")
                print(f"wrote {args.output}")
            else:
                print(text, end="")
        elif args.rules_command == "import":
            _import(session, source, args)
        elif args.rules_command == "check":
            print(f"rules on {args.name}: {check_rules(session, source).summary()}")
        else:  # add
            rule = add_user_rule(
                session, source, load_catalog(session, source),
                parse_spec(json.loads(args.rule_json)), args.by or getpass.getuser(), utcnow(),
            )  # fmt: skip
            print(f"added (active): {_line(rule)}")
    return 0


def run_rule_command(args: argparse.Namespace) -> int:
    with store_session() as session:
        if args.rule_command == "show":
            print(_describe(find_rule(session, args.id)))
            return 0
        # Look every id up first, so a typo changes nothing.
        rules = [find_rule(session, rule_id) for rule_id in args.ids]
        for rule in rules:
            review_rule(
                session,
                rule,
                _ACTIONS[args.rule_command],
                actor=args.by or getpass.getuser(),
                now=utcnow(),
                note=args.note,
            )
            print(f"{str(rule.id)[:8]} is now {rule.status.value}: {rule.name}")
    return 0


def _line(r: Rule) -> str:
    confidence = f"{r.confidence:.2f}" if r.confidence is not None else "  - "
    return f"{str(r.id)[:8]}  {r.status.value:8} {r.origin.value:6} {confidence}  {r.name}"


def _describe(r: Rule) -> str:
    lines = [
        r.name,
        f"  id:         {r.id}",
        f"  status:     {r.status.value}   origin: {r.origin.value}   by: {r.created_by}",
        f"  confidence: {r.confidence if r.confidence is not None else '-'}",
    ]
    if r.evidence.get("rationale"):
        lines.append(f"  rationale:  {r.evidence['rationale']}")
    lines += ["", "  definition:"]
    lines += [f"    {line}" for line in json.dumps(r.definition["spec"], indent=2).splitlines()]
    history = r.evidence.get("review_history", [])
    if history:
        lines += ["", "  history:"]
        lines += [
            f"    {h['at']}  {h['by']}: {h['from']} -> {h['to']}"
            + (f"  ({h['note']})" if h.get("note") else "")
            for h in history
        ]
    return "\n".join(lines)


def _import(session: Session, source: DataSource, args: argparse.Namespace) -> None:
    _, wanted = parse_rules_file(args.file.read_text(encoding="utf-8"))
    catalog = load_catalog(session, source)
    plan = plan_import(session, source, catalog, wanted)
    for line in plan.lines(prune=args.prune):
        print(line)
    pruned = len(plan.not_in_file) if args.prune else 0
    print(
        f"{plan.changes + pruned} changes, {plan.unchanged} unchanged, {len(plan.invalid)} unusable"
    )
    if not args.apply:
        if plan.changes + pruned:
            print("nothing changed yet: run again with --apply")
        return
    apply_import(session, source, catalog, plan, by=args.by or getpass.getuser(), now=utcnow(),
                 prune=args.prune)  # fmt: skip
    print("applied")


def run_check_command(args: argparse.Namespace) -> int:
    """`lumida check`: no metadata store needed; the structure is read from the database."""
    _, wanted = parse_rules_file(args.rules.read_text(encoding="utf-8"))
    settings = ScanSettings(
        include_schemas=tuple(_split(args.include_schemas)),
        exclude_schemas=tuple(_split(args.exclude_schemas)),
    )
    source = DataSource(
        name="check",
        kind=SourceKind(args.kind),
        connection_ref=args.connection_ref,
        settings=settings.model_dump(mode="json", exclude_defaults=True),
    )
    with open_adapter(source) as adapter:
        catalog = catalog_from_tables(source, adapter.introspect())
        outcomes = check_file(adapter, catalog, wanted, DEFAULT_RULE_SETTINGS.sample_size)
    broken = [o for o in outcomes if not o.ok]
    if args.json:
        print(json.dumps({"ok": not broken, "rules": [asdict(o) for o in outcomes]}, indent=2))
    else:
        for o in outcomes:
            if o.problem:
                print(f"ERROR   {o.rule}: {o.problem}")
            elif o.ok:
                print(f"ok      {o.rule}  ({o.checked_rows:,} rows)")
            else:
                ids = ", ".join(json.dumps(s) for s in o.sample[:3])
                sampled = " (sample)" if o.sampled else ""
                print(f"BROKEN  {o.rule}: {o.violating_rows:,} of {o.checked_rows:,} rows"
                      f"{sampled}" + (f"; e.g. {ids}" if ids else ""))  # fmt: skip
        print(f"{len(outcomes) - len(broken)} of {len(outcomes)} rules hold")
    return 1 if broken else 0


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]
