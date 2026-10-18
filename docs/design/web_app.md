# Design: the web app (Stage 4.4)

**Status:** built 2026-10-18 and checked by hand in a browser against the local test-lab store: logging in, every page, and creating and revoking an API key. The full suite passes (369 tests).
**Code:** `src/lumida/web/` (`index.html`, `app.js`, `app.css`, logo). It's served by the API (`api/app.py`), with cookie login in `api/auth.py`.

## 1. Goal
People work with Lumida in a browser instead of a terminal. They see how healthy each database is, go through the problems, confirm or reject them, approve AI-proposed rules, and answer Lumida's questions. Admins manage people and read the audit log.

## 2. Decisions
| | Decision |
|---|---|
| **Technology** | plain HTML, CSS and JavaScript (ES modules), the same as the website. There's no framework, no build step and no Node.js in the image: the files ship inside the Python package and the API serves them at `/`. |
| **One address** | the web app and the API share `http://<host>:8000`. Page addresses use `#` (`#/s/shop/rules`), so there's nothing extra to configure on the server. |
| **Login in the browser** | `POST /auth/login` with `cookie: true` puts the session in an **HttpOnly, SameSite=Strict** cookie. Page scripts can't read it, and the browser doesn't send it with requests that start on other sites. It's marked `Secure` automatically behind HTTPS. Scripts keep using tokens and API keys. |
| **CSRF** | a change (anything but GET) made with the cookie must also send `X-Lumida-CSRF: 1`. Another website can't add that header without our permission, and no cross-origin access (CORS) is configured. Without it the answer is 403. |
| **No injected markup** | every value from the API goes onto the page as text (`textContent`), never as HTML. |
| **Content Security Policy** | web pages are served with `script-src 'self'` (no inline scripts, nothing from other sites), `frame-ancestors 'none'` (never inside another site's frame), `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`. The API docs page (`/api/docs`) keeps its own. |
| **Role-aware** | buttons appear only for people allowed to use them: reviewers see review buttons, admins see People and Audit log. The API checks the role again anyway. |
| **Small screens** | it works at phone width; on narrow screens the header scrolls away instead of covering the page. |

## 3. Pages
| Address | What |
|---|---|
| `#/login` | email + password |
| `#/` | every database: health score, open problems by severity, last run, schedule |
| `#/s/<name>` | **Problems**, filtered by status, severity and kind, paged, with the health panel (schemas, worst tables, change since the previous run) |
| `#/s/<name>/rules` | rules, proposed first: approve, reject, disable, enable, reconsider; the definition and the AI's reasoning |
| `#/s/<name>/relationships` | links between tables (declared or inferred, confidence, AI opinion): confirm or reject |
| `#/s/<name>/questions` | Lumida's questions with a suggested answer: answer or "don't know" |
| `#/s/<name>/runs` | the last 30 nightly runs |
| `#/f/<id>` | one problem: description, evidence, AI explanation, history, and the review buttons with a note |
| `#/people` | admins: add people, change roles, disable or enable, reset passwords |
| `#/audit` | admins: the audit log, filtered by kind, paged |
| `#/me` | my API keys (made, shown once, revoked) and changing my password |

## 4. Tests
- **Automated:**
  - cookie login (HttpOnly, SameSite=Strict, no token in the answer)
  - a change without the CSRF header is refused (403) and allowed with it
  - logging out clears the session
  - the page is served with the strict policy and no inline scripts; every asset loads
  - `/api/docs` isn't affected
- **By hand** (2026-10-18, in the browser pane, against the local store with four lab databases):
  - logging in and the overview (scores, problem counts)
  - a database's problems with the health panel (its bars work under the strict policy)
  - the problem detail
  - rules, relationships, questions, runs
  - people, audit log, my account
  - an API key created and revoked from the browser
  - fixed along the way: on phone-width screens the sticky header covered the page

## 5. Not yet
- **Relationship map** (a diagram); for now a list. `lumida docs` still writes the Mermaid map.
- **Adding databases, "run now", AI proposals and explanations from the browser**: they need a job queue (4.5).
- **Charts of a column's history** (e.g. its null rate over time), and bulk review of many problems at once.
- **Notifications in the app**: alerts still go to Slack.
