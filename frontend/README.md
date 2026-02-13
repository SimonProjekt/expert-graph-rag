## Frontend Integration

This folder is prepared for a Lovable-generated frontend.

- Put exported Lovable source code in `frontend/lovable-src/`
  or import a zip with:

```bash
./scripts/import_lovable_export.sh /path/to/lovable-export.zip
```

- Build static assets into `frontend/static/` with:

```bash
./scripts/build_lovable_frontend.sh
```

Caddy serves the built frontend at:

- `https://<your-domain>/app`

Backend API stays on the same origin at:

- `https://<your-domain>/api/*`
