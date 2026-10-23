# Contributing

Thanks for your interest. This repository is a reference implementation using fictional demo data; it is not a production decision system.

## Development setup

```bash
cd platform
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd ../platform-web
npm install
```

Run checks before opening a pull request:

```bash
cd platform
python -m ruff check .
python -m pytest tests --cov=app --cov=core --cov=adapters --cov=domains --cov-fail-under=60
python -m mypy app core adapters domains   # existing baseline issues are tracked

cd ../platform-web
npm run typecheck
npm run build
```

## Pull requests

- Keep the change focused; describe the behaviour and the evidence (commands/logs) in the PR.
- Do not modify golden values, simulated data semantics, or delete assertions to make new code pass. If an existing test conflicts with a security requirement, raise the conflict in the PR instead of silently weakening the test.
- Do not commit secrets, real customer data, `.env` files, or internal materials.
- New endpoints require validation, authorization checks, tests, and consistent `{code,message,data}` responses.
- UI changes need a browser check; note explicitly if a browser check was not possible.

## Reporting security issues

Please follow [SECURITY.md](SECURITY.md); do not disclose vulnerabilities in public issues.

## License

Contributions are accepted under the GNU General Public License v3.0 or later, matching the repository license.
