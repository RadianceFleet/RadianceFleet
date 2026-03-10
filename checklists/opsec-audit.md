# RadianceFleet Opsec Audit Checklist

Last full audit: 2026-03-10

---

## Automated Checks (CI)

These run automatically on every push/PR via `secrets-scan` job.

| # | Check | Tool | Status | Last Checked |
|---|-------|------|--------|--------------|
| A1 | No hardcoded secrets in source | gitleaks | PASS | 2026-03-10 |
| A2 | Git author emails are all noreply | opsec-check.sh | PASS | 2026-03-10 |
| A3 | No office documents tracked | opsec-check.sh | PASS | 2026-03-10 |
| A4 | No image EXIF metadata leaks | opsec-check.sh | PASS | 2026-03-10 |
| A5 | No real names in test fixtures | opsec-check.sh | PASS | 2026-03-10 |

---

## GitHub & Source Control

| # | Check | Status | Last Checked |
|---|-------|--------|--------------|
| G1 | All commits use noreply email | PASS | 2026-03-10 |
| G2 | No personal email in tracked files | PASS | 2026-03-10 |
| G3 | `.env` correctly gitignored, never committed | PASS | 2026-03-10 |
| G4 | CI secrets handled properly (no echo/log) | PASS | 2026-03-10 |
| G5 | No hardcoded credentials in source | PASS | 2026-03-10 |
| G6 | GitHub profile review — no real name/photo/personal links | TODO | |
| G7 | Repository visibility settings reviewed | TODO | |
| G8 | Branch protection rules configured | TODO | |

---

## File Metadata

| # | Check | Status | Last Checked |
|---|-------|--------|--------------|
| F1 | No office docs/PDFs tracked | PASS | 2026-03-10 |
| F2 | No images with EXIF data in tracked files | PASS | 2026-03-10 |
| F3 | No screenshots committed (railway-web-check.png removed) | PASS | 2026-03-10 |
| F4 | Git history clean of accidentally committed secrets | TODO | |

---

## Accounts & Services

| # | Check | How to Verify | Status | Last Checked |
|---|-------|---------------|--------|--------------|
| S1 | All accounts use project email (not personal) | Manual review | TODO | |
| S2 | 2FA enabled on all service accounts | Manual review | TODO | |
| S3 | Password manager used for all credentials | Manual review | TODO | |
| S4 | Payment methods do not leak personal identity | Manual review | TODO | |
| S5 | API keys rotated on schedule | Manual review | TODO | |
| S6 | Unused service accounts deactivated | Manual review | TODO | |

---

## Hosting

| # | Check | How to Verify | Status | Last Checked |
|---|-------|---------------|--------|--------------|
| H1 | Railway account uses project email | Check Railway settings | TODO | |
| H2 | No personal info in Railway environment variables | `railway variables` | TODO | |
| H3 | Server logs do not contain PII | Review log output | TODO | |
| H4 | Error reporting (Sentry) redacts sensitive fields | Check Sentry config | TODO | |

---

## Domain & Web

| # | Check | How to Verify | Status | Last Checked |
|---|-------|---------------|--------|--------------|
| D1 | WHOIS privacy enabled | `whois radiancefleet.com` | TODO | |
| D2 | SSL certificate does not leak org info | `openssl s_client -connect radiancefleet.com:443 < /dev/null 2>/dev/null \| openssl x509 -noout -subject -issuer` | TODO | |
| D3 | DNS TXT records clean | `dig radiancefleet.com TXT` | TODO | |
| D4 | No analytics/tracking that ties to personal accounts | Review page source | TODO | |
| D5 | robots.txt does not expose sensitive paths | `curl https://radiancefleet.com/robots.txt` | TODO | |

---

## Communications

| # | Check | How to Verify | Status | Last Checked |
|---|-------|---------------|--------|--------------|
| C1 | Project communications use secure channels | Manual review | TODO | |
| C2 | No real names in public issue tracker | Review GitHub Issues | TODO | |
| C3 | PR descriptions do not contain sensitive operational details | Review recent PRs | TODO | |

---

## Periodic Actions

Perform these on a regular schedule:

| Action | Frequency | Last Done | Next Due |
|--------|-----------|-----------|----------|
| Full opsec audit (this checklist) | Monthly | 2026-03-10 | 2026-04-10 |
| Rotate API keys and tokens | Quarterly | TODO | |
| Review GitHub access permissions | Quarterly | TODO | |
| Search git history for leaked secrets | Monthly | 2026-03-10 | 2026-04-10 |
| Review and prune CI/CD secrets | Quarterly | TODO | |
| Check WHOIS privacy renewal | Annually | TODO | |
| Review third-party service access | Quarterly | TODO | |
