# Observability Architecture

Grayhaven Systems LLC production hosts can optionally publish metrics, logs, and
managed alert rules to Grafana Cloud.

## Table of Contents

- [Design](#design)
- [Enablement](#enablement)
- [Metrics](#metrics)
- [Logs](#logs)
- [Managed Alerts](#managed-alerts)
- [Operational Boundaries](#operational-boundaries)

## Design

Observability is configured by Ansible because it depends on runtime host
inventory, vault values, managed services, and files created during
convergence. OpenTofu applies the DigitalOcean tags that decide whether a
production host should participate.

Grafana Cloud is supported only for the `prod` environment at this time. If it
is enabled in another environment, convergence fails before installing or
syncing observability components.

The DigitalOcean metrics agent remains installed on managed hosts. Those
metrics are useful as a secondary provider-side view when Grafana Cloud is not
enabled, but Grafana Cloud is the primary alerting path when enabled.

[Back to top](#observability-architecture)

## Enablement

`grayhaven-vault` supplies the Grafana Cloud endpoint and credential values.
The public
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository documents the expected
[configuration schema](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/schema.md#observability)
and
[Grafana Cloud setup](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/grafana-cloud-setup.md).

When Grafana Cloud is enabled, Ansible expects production hosts to carry the
`alerts-in-grafana-cloud` tag. Log shipping additionally requires
`logs-to-grafana-cloud`.

[Back to top](#observability-architecture)

## Metrics

Ansible installs Grafana Alloy on tagged production hosts and configures it to
publish Prometheus-compatible metrics to Grafana Cloud.

Metrics include:

- host CPU, memory, swap, filesystem, and disk statistics;
- managed systemd service state;
- Grayhaven Systems LLC host inventory metadata;
- active control-node metadata;
- Ansible convergence status;
- restic backup status;
- HTTP, HTTPS, redirect, basic-auth, and certificate probes for configured web
  domains.

HTTPS availability, development basic-auth, and certificate-expiry probes check
site behavior while allowing untrusted certificate chains. Separate
certificate-trust probes validate whether the presented certificate is trusted
by the external probe. This keeps staging-certificate deployments from looking
unavailable while still surfacing explicit untrusted-certificate alerts.

The active control node publishes the full known-host inventory as textfile
metrics so dashboards and alert rules can reason about all expected hosts.

[Back to top](#observability-architecture)

## Logs

Log shipping is optional. When enabled, Ansible configures Alloy to publish
selected logs to Grafana Cloud Loki.

The log set is intentionally targeted:

- Nginx access and error logs for configured domains;
- audit logs;
- Certbot logs;
- systemd journal entries, including sudo and `grayhaven-root-command` command
  audit entries.

Cloud-init logs are excluded. Log lines pass through a defensive redaction
stage for common secret-like key names before leaving the host, but redaction is
a safeguard, not a handling procedure. Administrators should still avoid placing
secrets directly on command lines or in log output. If a secret is exposed,
rotate it immediately.

Sudo session-open/session-close noise and Ansible automation sudo command lines
are filtered from shipped logs so the audit stream stays focused on human
operator activity. Ansible runner service logs are still shipped when log
shipping is enabled.

[Back to top](#observability-architecture)

## Managed Alerts

The active control node syncs Grafana Cloud alert rules during convergence.
Managed alert rules are labeled `configured_by=ansible`; Ansible only creates,
updates, or deletes rules carrying that label for the configured client. Manual
Grafana Cloud alert rules are left alone as long as they do not use that label.
Grafana Cloud does not allow API-provisioned alert rules to be modified or
deleted through the Grafana Cloud web interface, so managed alert rule changes
must go through Ansible. For planned maintenance, use Grafana Cloud silences as
documented in
[Operations](operations.md#grafana-cloud-observability).

On first convergence for a new control node, Ansible creates a short Grafana
Cloud silence matching the managed alert labels `configured_by=ansible`,
`client=grayhaven`, and `environment=prod` before syncing managed alert rules.
The silence gives new hosts time to settle and gives Alloy time to send initial
telemetry before alert evaluation begins. A local marker prevents normal
convergence from creating the silence again on the same control node.

Managed alerts cover the same operational checks surfaced by the Grafana
dashboards where alerting is useful, including host metrics, service state,
backup freshness, Ansible convergence, web availability, development
basic-auth behavior, certificate expiration, and certificate trust. CPU alerts
use a five-minute rate window and require five minutes above threshold before
firing. Alert rules send to the configured Grafana IRM contact point.

Normal threshold and probe alerts treat missing query data as OK so a telemetry
gap does not make every managed alert fire at once. Per-host metrics-data
alerts are the dedicated reachability and telemetry health checks. They fire
when the expected host `up` metric is missing or below threshold, such as when
Alloy is stopped, blocked, or unable to send host metrics to Grafana Cloud.
While a host is not sending usable metrics, that host's other managed threshold
alerts remain normal on missing data. A metrics-data alert therefore means the
on-call operator should first restore or investigate the host telemetry path;
it has priority over downstream host-specific checks until metrics are flowing
again.

[Back to top](#observability-architecture)

## Operational Boundaries

Grafana dashboards, on-call schedules, escalation chains, outgoing webhooks,
and notification templates are configured in Grafana Cloud. They are not
managed by this repository.

Changing Grafana Cloud enablement or log shipping requires a normal Ansible
convergence run. When alert rules are updated, the sync helper honors Grafana
Cloud API rate-limit responses before retrying.

[Back to top](#observability-architecture)
