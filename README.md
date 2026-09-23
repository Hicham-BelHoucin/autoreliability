# Automotive Reliability pSEO Engine

This repository provides a PostgreSQL-backed automotive reliability data pipeline and an Astro SSR frontend.

## Run locally

1. Copy `.env.example` to `.env` and replace `POSTGRES_PASSWORD`.
2. Set `VEHICLE_TARGETS` to the makes, models, and years to ingest.
3. Run `docker compose up --build`.

PostgreSQL initializes the partitioned schema and materialized view on its first start. The worker syncs the selected NHTSA complaint records immediately and repeats at `SYNC_INTERVAL_SECONDS`. The frontend is available at `http://localhost:3000`.

## Development mode

Use Astro hot module replacement while keeping PostgreSQL in Docker:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build postgres web
```

The `web` service bind-mounts `./web` and uses polling for reliable Windows file-change detection; saving an Astro, TypeScript, or CSS file refreshes the browser without rebuilding or restarting the container. Stop it with `Ctrl+C`.

## Operations

The worker writes individual malformed source records and failed target fetches to `ingestion_dlq`, so one bad payload does not terminate a batch. Upserts are keyed by `(make, model, year)` and refresh the leaderboard materialized view after a successful batch. Run `database/refresh_leaderboard.sql` outside a transaction when refreshing the view manually.

For a smoke-test batch, run `docker compose run --rm worker python -m pipeline.ingest --seed seeds.json --limit 2`. The repository seed file contains 30 high-volume US-market vehicle targets from model years 2016–2022.

## Production deployment

The production stack is an Astro SSR server behind Caddy, PostgreSQL, the ingestion worker, and a daily database backup job. It is designed for a Linux VPS with Docker Compose installed.

1. Point the chosen domain's `A` and `AAAA` records to the server before starting Caddy. Ports `80`, `443/tcp`, and `443/udp` must be reachable from the internet.
2. Copy `.env.example` to a protected `.env` file on the server. Set a unique database password, the final `https://` `SITE_URL`, `DOMAIN`, and `ACME_EMAIL`.
3. Create a Resend account, verify the sending domain, then set `RESEND_API_KEY`, `CONTACT_FROM_EMAIL`, and `CONTACT_TO_EMAIL`. The contact form returns a clear temporary-unavailable message until these are set.
4. Create a Plausible site for the final domain and set `PUBLIC_PLAUSIBLE_DOMAIN` to enable privacy-friendly analytics. Leave it blank to disable analytics.
5. Start production services:

```bash
docker compose -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.production.yml ps
```

`database-backup` writes one PostgreSQL custom-format dump per day to the `postgres_backups` volume and removes dumps older than `BACKUP_RETENTION_DAYS`. Copy that volume to encrypted off-server storage on a separate schedule; a backup on the same server does not protect against server loss.

After the domain is live, verify `https://your-domain.com/sitemap.xml`, `https://your-domain.com/robots.txt`, and a report page. Add the domain property to Google Search Console, submit the sitemap there, and confirm that report-detail URLs ending in `/reports` have `noindex,follow` while the main report pages with at least 10 complaints can be indexed.
