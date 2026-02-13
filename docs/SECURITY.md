# Public Demo Hardening Checklist

Use this checklist before exposing the demo to recruiters/public traffic.

- [ ] `DEBUG` is `false` in `.env.prod`.
- [ ] `DJANGO_SECRET_KEY` is unique, long, and rotated from any default/dev value.
- [ ] `DJANGO_ALLOWED_HOSTS` only includes your real demo domain(s).
- [ ] `DJANGO_CSRF_TRUSTED_ORIGINS` is set to your HTTPS domain.
- [ ] `/api/ask` is rate-limited (add DRF throttling and/or Caddy request limits).
- [ ] `/admin` and `/debug/*` are protected (Caddy basic auth, IP allowlist, VPN, or both).
- [ ] Neo4j is not publicly exposed (no `ports` mapping for neo4j in production compose).
- [ ] Postgres and Redis are not publicly exposed.
- [ ] TLS is active (Caddy automatic certificates) and HTTP redirects to HTTPS.
- [ ] Backups are enabled and tested (`scripts/prod_backup.sh`, `scripts/prod_restore.sh`).
- [ ] `.env.prod` is present only on server and never committed.

## Additional Notes

- Demo mode works without `OPENAI_API_KEY`; keep the key empty if you do not want paid API usage.
- Keep the VPS patched (`apt update && apt upgrade`) and review logs regularly.
