# Infrastructure

Docker configs, deployment recipes, monitoring stack.

## Layout

```
infra/
├── docker/                 Dockerfiles (gateway, venue, frontend) + nginx config
├── compose/                docker-compose files (full stack, dev overrides, infra-only)
├── cloud/
│   └── aws/                EC2 setup scripts, security group configs
└── monitoring/
    ├── prometheus.yml      Metrics scraping
    └── grafana/dashboards/ Pre-built dashboards
```

> **AWS only.** The original docs described AWS+GCP+Azure for the multi-cloud venue narrative. The actual deployment plan is single-cloud — AWS with separate VMs per venue/environment. Do not add `gcp/` or `azure/` folders.

## What's empty right now (and when it fills)

| Path | Filled when |
|---|---|
| `docker/` | M3 — POC runs Python directly, no containers needed |
| `compose/` | M3 — same reason |
| `cloud/aws/` | M3/M4 — when we actually deploy. Local-first for POC. |
| `monitoring/` | M4 — observability comes after the system works |

## When you're deploying (M3+)

See [`../project-info/DEPLOYMENT.md`](../project-info/DEPLOYMENT.md) §3 for the venue-deployment recipes. Ignore the GCP and Azure sections — adapt the AWS instructions for every venue.
