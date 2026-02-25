# Security Policy

## Threat Model

RadianceFleet is designed for journalists, OSINT researchers, and NGO analysts working to expose
Russian shadow fleet activity. The tool's output — AIS position histories, watchlist hits,
analyst annotations, gap anomaly reports — can put sources and analysts at risk in certain
operating environments.

**Primary threat scenarios:**

- A journalist in a hostile jurisdiction whose workstation is seized. Stored AIS tracks and
  OFAC/KSE watchlist hit results must not be accessible to an adversary with physical access
  to the machine.
- An NGO team operating from a country subject to sanctions enforcement. The tool's network
  activity (satellite API calls, external data fetches) may draw scrutiny. Minimize outbound
  requests to what is strictly necessary for the analysis task.
- A shared server deployment where multiple analysts work on the same database. Without an
  auth layer, any user with network access to port 8000 can read all vessel data and
  watchlist hits. See the multi-user section below.

**What this tool does not protect against:**

- Full-disk compromise of the host machine. Use OS-level full-disk encryption (LUKS, BitLocker,
  FileVault) in addition to the guidance here.
- Network interception of unencrypted HTTP traffic. Run behind TLS in any network-exposed
  deployment.
- Supply-chain attacks on Python dependencies. Pin dependencies with `uv lock` and verify
  the lockfile before deployment in sensitive environments.

---

## Sensitive Data Handled

| Data type | Sensitivity | Location |
|---|---|---|
| AIS position history (lat/lon, timestamps) | High — reveals vessel routes | `ais_positions` table |
| OFAC / KSE / OpenSanctions watchlist hits | High — links vessels to sanctions targets | `watchlist_matches` table |
| Analyst notes and status labels | Medium — reveals investigative focus | `alerts` table |
| Gap anomaly records | Medium — operational intelligence | `gap_events` table |
| STS event records | Medium | `sts_events` table |
| Spoofing anomaly records | Medium | `spoofing_anomalies` table |
| Commercial satellite API keys | Critical — billable credentials | `.env` file only |

Treat all database exports and CSV alert exports as you would treat raw source material.
Store them encrypted at rest and transmit only over encrypted channels.

---

## No API Authentication in MVP

The current release is a **single-user MVP with no authentication layer** on the FastAPI
backend. The API server binds to `127.0.0.1:8000` by default (via `make dev`). This means:

- Requests from other machines on the same network cannot reach the API unless you explicitly
  change the bind address.
- Anyone with local access to the machine can query all endpoints without a credential.

**Do not expose port 8000 to the internet or to an untrusted LAN without adding an auth layer.**

### Adding authentication for multi-user deployments

Place an nginx reverse proxy in front of the API and require a bearer token header. A minimal
nginx configuration block:

```nginx
server {
    listen 443 ssl;
    server_name radiancefleet.yourdomain.example;

    ssl_certificate     /etc/ssl/certs/radiancefleet.crt;
    ssl_certificate_key /etc/ssl/private/radiancefleet.key;

    location /api/ {
        # Require a shared bearer token. Generate with: openssl rand -hex 32
        if ($http_authorization != "Bearer YOUR_SECRET_TOKEN_HERE") {
            return 401;
        }
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        root /var/www/radiancefleet;
        try_files $uri /index.html;
    }
}
```

Future releases will add per-user authentication natively. For now, the nginx token approach
provides a single shared credential that is sufficient for a small trusted team.

---

## Secret Management

All credentials and keys are loaded from the `.env` file in the `backend/` directory (or from
environment variables in production).

**Rules:**

1. **Never commit `.env` to git.** The repository's `.gitignore` must include `.env`. Verify
   with `git status` before every commit that no `.env` file is staged.
2. Copy `.env.example` to `.env` and fill in real values locally. The example file contains
   only placeholders and is safe to commit.
3. If a key is accidentally committed, treat it as compromised immediately. Rotate the key
   at the provider, then remove it from git history with `git filter-repo` or BFG Repo Cleaner.
4. Satellite API keys (Capella, Planet, Umbra) and the OpenSanctions API key are billable or
   rate-limited credentials. Store them only in `.env` and never log them. Verify your logging
   configuration does not emit the full settings object at startup.
5. In Docker deployments, pass secrets via environment variables in `docker-compose.yml` using
   the `env_file` directive, not as literal values in the compose file itself.

Example `docker-compose.yml` env_file usage (add to the `postgres` service if needed):

```yaml
services:
  postgres:
    env_file:
      - .env
```

---

## PostgreSQL Hardening

The default `docker-compose.yml` uses:

- Username: `radiancefleet`
- Password: `radiancefleet`
- Database: `radiancefleet`

These defaults are acceptable for local development on a machine with no network exposure.
For any deployment that is reachable from a network, change the password before first use:

```bash
# After starting the container, connect as superuser and change the password
docker exec -it radiancefleet_db psql -U radiancefleet -c \
  "ALTER USER radiancefleet PASSWORD 'replace-with-a-strong-random-password';"
```

Update `DATABASE_URL` in `.env` to match the new password.

**Additional hardening for network-exposed deployments:**

- Bind PostgreSQL to localhost only. In `docker-compose.yml`, change the port mapping from
  `"5432:5432"` to `"127.0.0.1:5432:5432"` so the port is not reachable from other hosts.
- Disable the `pgadmin` service in production. It is gated behind `--profile debug` by default,
  so it will not start unless explicitly requested.
- Enable PostgreSQL SSL if analysts connect over a network. Set `ssl = on` in `postgresql.conf`
  and use `sslmode=require` in `DATABASE_URL`.
- Rotate the PostgreSQL password on a regular schedule and revoke access for analysts who leave
  the team.

---

## Vulnerability Disclosure

RadianceFleet follows a **90-day responsible disclosure policy**.

- Report vulnerabilities by email to: `security@radiancefleet.org`
- Include a description of the vulnerability, steps to reproduce, and any proof-of-concept code.
- We will acknowledge receipt within 5 business days.
- We will provide a remediation timeline within 14 days.
- After 90 days from initial report, you may disclose publicly regardless of patch status,
  unless we have agreed on an extension.
- There is no bug bounty program at this time.

**Scope:** The RadianceFleet backend API, CLI, data models, and Docker configuration.
Out of scope: Third-party dependencies (report those to their upstream maintainers), the
operating system and infrastructure, social engineering.

**Please do not open public GitHub issues for security vulnerabilities.** Use the email
address above.
