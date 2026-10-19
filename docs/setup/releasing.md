# Releasing Lumida (for the maintainer)

A release publishes two things from one git tag. Customers install either one; neither needs the source code or any server of ours.
- **Docker image:** `ghcr.io/rohan708/lumida:<version>` (+ `:latest`)
- **Python package:** `pip install lumida` from PyPI

## One-time setup

### 1. PyPI ("trusted publishing": no password or token stored anywhere)
1. Create an account on https://pypi.org and turn on two-factor login.
2. Check the name is free: https://pypi.org/project/lumida shows "Not Found". If it's taken, change `name` in `pyproject.toml` (e.g. `lumida-data`); the command stays `lumida`.
3. Go to https://pypi.org/manage/account/publishing/ → *Add a new pending publisher*:
   - PyPI project name: `lumida`
   - Owner: `Rohan708` · Repository: `Lumida`
   - Workflow name: `release.yml` · Environment name: `pypi`
4. On GitHub, go to the repo → *Settings → Environments → New environment* and name it `pypi`. Optionally require your approval before each publish.

### 2. The Docker image's visibility
After the first release: GitHub profile → *Packages → lumida → Package settings → Change visibility → Public*. While it's private, only people you give access to can pull it.

### 3. Licence
Apache 2.0 (`LICENSE`, and `license = "Apache-2.0"` in `pyproject.toml`). Contributions come under the same licence.

## Each release
1. Set the version in `src/lumida/__init__.py` (e.g. `0.4.1`), commit and push. The workflow refuses a tag that doesn't match it.
2. Tag and push:
   ```bash
   git tag v0.4.1
   git push origin v0.4.1
   ```
3. Watch *Actions → Release*: it checks the version, then builds, smoke-tests and publishes the image and the package (about 5 minutes).
4. Test it like a customer, from an empty folder with no repository (next section).

## Testing a release like a customer
**pip:**
```bash
python -m venv lumida-test
lumida-test\Scripts\activate          # macOS/Linux: source lumida-test/bin/activate
pip install lumida
lumida setup
lumida serve
```
Then open http://127.0.0.1:8000.

**Docker:** download `deploy/docker-compose.yml` and `deploy/.env.example` into an empty folder, then:
```bash
copy .env.example .env               # macOS/Linux: cp .env.example .env
docker compose up -d
docker compose exec -it lumida lumida user add you@example.com --role admin
```
Then open http://127.0.0.1:8000.

**No internet at the customer (air-gapped):**
```bash
docker pull ghcr.io/rohan708/lumida:0.4.1
docker save ghcr.io/rohan708/lumida:0.4.1 -o lumida-0.4.1.tar
```
They copy the file over and run `docker load -i lumida-0.4.1.tar`, then `docker compose up -d`.
