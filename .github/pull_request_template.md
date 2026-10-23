## Summary

- What changed and why (1–3 sentences)

## Evidence

- [ ] Backend tests: `cd platform && python -m pytest tests --cov=app --cov=core --cov=adapters --cov=domains --cov-fail-under=60`
- [ ] Lint: `python -m ruff check .`
- [ ] Types: `python -m mypy app core adapters domains` (or note existing baseline)
- [ ] Frontend: `cd platform-web && npm run typecheck && npm run build`
- [ ] UI browser check (or explain why not possible)

## Risk / compatibility

- Security, authorization, data migration or API changes:
- Golden values / simulated data / existing assertions untouched? (explain any intentional contract change)

## Limitations

- What is intentionally not included in this PR
