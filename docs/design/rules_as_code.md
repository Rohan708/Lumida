# Design: rules as code, managing databases, packaging (Stage 4.6)

**Status:** built 2026-10-18. Lint, types and the unit tests are clean. The Docker image and the SQL Server licence guard were checked locally. The integration tests are pending the user's run.
**Code:**
- `rules/as_code.py`, with `lumida rules export|import` and `lumida check` in `rules/cli.py`
- `PATCH`/`DELETE /sources/{name}` and `GET /sources/{name}/rules/export` in `api/onboarding_routes.py`
- the web app's *Settings* tab and its new look
- `Dockerfile` (SQL Server option), `.github/workflows/release.yml`

## 1. Rules as code
Approved rules live in the app, but teams also want them where their code is: reviewed in pull requests and checked in CI.

```yaml
# Lumida rules for shop. …
version: 1
source: shop
rules:
- rule: legacy.INV_LINE.QTY > 0          # the readable form (rewritten on export)
  status: active                         # active | disabled
  origin: ai                             # informative
  approved_by: maria@shop.example        # informative
  spec: {kind: compare_constant, table: legacy.INV_LINE, column: QTY, op: '>', value: 0.0}
```

| | Decision |
|---|---|
| **What's exported** | active and disabled rules, which are decisions people made. AI proposals and rejected rules stay in the app. |
| **Stable files** | rules are sorted by their readable form, with no dates in the file, so exporting twice gives the same file and a change shows up as a small diff. |
| **Importing** | `lumida rules import shop FILE` only **shows** the changes (`+ add`, `~ switch on/off`, `! can't use`). `--apply` makes them, through the same functions as a review in the app, so each change is validated and recorded in the rule's history ("from a rules file"). |
| **Rules missing from the file** | kept as they are, unless `--prune` is given; then they're switched off, never deleted. |
| **Malformed files** | refused as a whole, with the rule number and the reason, so nothing is half-loaded. |
| **The file holds** | rule definitions only, never row values or connection details. |
| **Download** | the web app's *Rules* tab has *Download as YAML* (`GET /sources/{name}/rules/export`). |

### `lumida check`: a rules file in CI
```bash
lumida check --rules lumida-rules.yaml --connection-ref STAGING_DB_URL [--kind mysql] [--exclude-schemas x] [--json]
```
- It **doesn't need Lumida's own database**: the structure is read live from the target. It checks each active rule read-only, and only counts and row IDs come back.
- **Exit code 1** when a rule is broken or can't run (e.g. its table is gone), so the pipeline fails before bad data reaches production.
- The output shows `ok` / `BROKEN` / `ERROR` per rule, with counts and a few primary keys. `--json` gives the same for tools.

Example (GitHub Actions, against a staging database):
```yaml
- run: pip install lumida            # or use the Lumida image
- run: lumida check --rules lumida-rules.yaml --connection-ref STAGING_DB_URL
  env:
    STAGING_DB_URL: ${{ secrets.STAGING_DB_URL }}
```

## 2. Managing a database in the browser (admins)
The *Settings* tab on each database has:
- **Schedule and scope:** time, time zone, which schemas (`PATCH /sources/{name}`, validated like `lumida source add`).
- **Pause:** no nightly runs until it's resumed; *Run now* still works and nothing is deleted.
- **Remove:** you type the database's name to confirm (`DELETE /sources/{name}?confirm=name`). It forgets everything Lumida learned (history, findings, rules, answers, jobs); the database itself is never touched.

All three are in the audit log.

## 3. The web app's look
The same "Luminous Void" design as the website:
- a near-black canvas, white type and buttons, one champagne accent
- hairline borders and the new aperture logo
- fonts are the system's (Geist if installed), because the app's security policy loads nothing from other sites

## 4. Packaging
| | Decision |
|---|---|
| **SQL Server image** | `docker build --build-arg MSSQL=yes --build-arg ACCEPT_MSSQL_EULA=yes .` adds Microsoft's ODBC Driver 18 from Microsoft's own package source. Without the licence argument, the build stops with the licence link: it's never accepted silently. |
| **CI** | always builds the standard image, checks the web app ships inside it, and checks the SQL Server option refuses to build without the licence. The full SQL Server image builds only once the repository owner sets the variable `MSSQL_EULA_ACCEPTED=yes`. |
| **Publishing** | `release.yml` publishes `ghcr.io/<owner>/lumida:<version>` and `:latest` when a tag like `v0.4.0` is pushed. Nothing is published before the first tag. The SQL Server variant isn't published, because of the licence; customers build it. |

## 5. Tests
- **Unit:** the endpoint list.
- **Integration:**
  - export is stable and imports back with no changes
  - an edited file (one rule off, one dropped, one added, one unusable) shows exactly those changes, and with `--prune` applies them, recording "from a rules file" in each rule's history
  - malformed files are refused
  - `lumida check` on the lab: exit 0 when the rules hold; exit 1 with counts and primary keys when one is broken; exit 1 when a rule's table is missing
  - API: change the schedule (admins only, bad time zones refused), pause and resume, download the rules, remove only with the name repeated (its jobs go with it), audited
- **By hand:** the image builds and ships the web app; the SQL Server option refuses to build without the licence.

## 6. Not yet
- **Rule changes as pull requests** (Lumida opening a PR on the customer's repo).
- **Per-environment files** (staging vs production thresholds).
- **A signed image and SBOM** for security-conscious customers.
