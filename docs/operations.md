# Operations

Grayhaven Systems LLC configuration is normally applied by the active control
bastion. This document covers manual runner use and maintenance playbooks.

## Table of Contents

- [Manual Runner Invocation](#manual-runner-invocation)
- [Runner And Poller Status](#runner-and-poller-status)
- [Manual Discord Notification Test](#manual-discord-notification-test)
- [Vault Password Rotation](#vault-password-rotation)
- [Deploy Key Rotation](#deploy-key-rotation)
- [Ansible Control Key Rotation](#ansible-control-key-rotation)
- [Triggering a Manual Backup](#triggering-a-manual-backup)
- [Verifying Local Backups](#verifying-local-backups)
- [Verifying Remote Backups](#verifying-remote-backups)
- [Listing the Contents of a Local Backup](#listing-the-contents-of-a-local-backup)
- [Listing the Contents of a Remote Backup](#listing-the-contents-of-a-remote-backup)
- [Restoring a Local Backup](#restoring-a-local-backup)
- [Restoring a Remote Backup](#restoring-a-remote-backup)
- [Root Command Audit Trail](#root-command-audit-trail)
- [Operator Tmux Console](#operator-tmux-console)
- [Grafana Cloud Observability](#grafana-cloud-observability)
- [Infrastructure Policy Changes](#infrastructure-policy-changes)

## Manual Runner Invocation

Connect to the active control bastion through the easy `bastion.*` DNS record,
then start the runner:

```bash
sudo systemctl start grayhaven-ansible-runner.service
```

The runner refreshes the public configuration checkout and `grayhaven-vault`
checkout, loads vault values, refreshes live DigitalOcean inventory, prepares
SSH known hosts, and runs `playbooks/site.yml`.

Manual commands that use the DigitalOcean inventory require
`GRAYHAVEN_ENVIRONMENT` to be set. If it is unset, inventory selection fails
closed instead of defaulting to an environment.

[Back to top](#operations)

## Runner And Poller Status

Useful service and log commands:

```bash
sudo systemctl status grayhaven-ansible-runner.service
sudo systemctl status grayhaven-ansible-runner.timer
sudo systemctl status grayhaven-ansible-poller.service
sudo systemctl status grayhaven-ansible-poller.timer
sudo systemctl status grayhaven-reboot-notify.service
sudo journalctl -u grayhaven-ansible-runner.service
sudo journalctl -u grayhaven-ansible-poller.service
sudo journalctl -u grayhaven-reboot-notify.service
```

The poller checks the tracked configuration and vault refs every five minutes.
When it detects a change, it starts the runner and then records the observed
refs. If the runner trigger fails, the ref state is not advanced so the next
poll can retry the same change.

Managed hosts send one informational `Server Rebooted` Discord notification
after each boot. The local `grayhaven-reboot-notify.service` records the current
boot ID after a successful notification so repeated service checks do not resend
the same reboot event.

[Back to top](#operations)

## Manual Discord Notification Test

Use a test webhook when manually checking Discord notification formatting. The
manual request should include an explicit user agent because Discord may reject
default client user agents.

```bash
WEBHOOK_URL="<insert webhook URL here>"

curl \
  --silent \
  --show-error \
  --request POST \
  --header "Content-Type: application/json" \
  --user-agent "GrayhavenSystemsLLC/1.0 (+https://grayhavensystems.com)" \
  --data @- \
  "$WEBHOOK_URL" <<'JSON'
{
  "embeds": [
    {
      "title": "Server Rebooted",
      "description": "manual-test.grayhavensystems.com\n\nEnvironment: test\n2026-06-12 01:00 PM CDT",
      "color": 5811424
    }
  ]
}
JSON
```

Discord returns HTTP `204` when the webhook accepts the notification.

[Back to top](#operations)

## Vault Password Rotation

Vault password rotation has three coordinated parts:

1. Rekey the encrypted files in `grayhaven-vault` for the target
   environment branch by following the
   [vault password rotation documentation](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/operations.md#vault-password-rotation)
   in the
   [`grayhaven-vault-example`](https://github.com/dean1012/grayhaven-vault-example)
   repository.
2. Update the matching variable in
   [`grayhaven-infra-opentofu`](https://github.com/dean1012/grayhaven-infra-opentofu),
   `TF_VAR_grayhaven_vault_password_staging` or
   `TF_VAR_grayhaven_vault_password_prod`, by following the
   [Ansible vault passphrase rotation documentation](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/operations.md#ansible-vault-passphrase-rotation)
   in the `grayhaven-infra-opentofu` repository so future droplets bootstrap
   with the new password.
3. Rotate the persisted password already stored on deployed bastions.

After the vault files and `grayhaven-infra-opentofu` environment variables are
updated, rotate the persisted vault password by placing the new value in a
temporary vars file and passing that file with `--extra-vars`:

```bash
ansible-playbook \
  --inventory inventory \
  playbooks/rotate-vault-password.yml \
  --extra-vars @/path/to/temp-vault-password.yml
```

The temporary vars file should contain:

```yaml
new_vault_password: "<new password>"
```

Avoid passing the new password directly on the shell command line. Remove the
temporary vars file after the playbook completes and verify a runner invocation
can decrypt the vault with the new password:

```bash
sudo systemctl start grayhaven-ansible-runner.service
sudo journalctl -u grayhaven-ansible-runner.service
```

[Back to top](#operations)

## Deploy Key Rotation

Rotate the vault deployment SSH keypair with
`playbooks/rotate-vault-deploy-key.yml`. OpenTofu supplies this keypair during
first boot so bastions can read `grayhaven-vault`.
After full convergence, bastions keep it only for `grayhaven-vault` repository
access at `/home/ansible/.ssh/grayhaven_vault_deploy_key`.

The deployment SSH keypair is shared by workspace environments. Run this
maintenance playbook from the active control bastion in each deployed
environment that has existing bastions retaining the key.

Place the staged files on bastion hosts before running the playbook:

- `/home/ansible/new_ansible_deploy_key`
- `/home/ansible/new_ansible_deploy_key.pub`

The files should be owned by `ansible:ansible`; the private key should be mode
`0600`, and the public key should be mode `0644`.

Run the playbook from the active control bastion:

```bash
ansible-playbook --inventory inventory playbooks/rotate-vault-deploy-key.yml
```

After the playbook completes, verify the runner can refresh the public
configuration repository and `grayhaven-vault`, then run a full
convergence pass. This playbook does not change the Ansible control key used
for managed-host SSH. The playbook removes the staged key files from bastions.

[Back to top](#operations)

## Ansible Control Key Rotation

Normal convergence enforces the vault-defined Ansible control key. After
updating `ansible_control_public_key` and `ansible_control_private_key` in the
encrypted `grayhaven-vault` data, run the normal runner service from the active
control bastion.

The maintenance playbook remains available when you need to rotate the control
key directly from encrypted vault values:

```bash
ansible-playbook \
  --inventory inventory \
  playbooks/rotate-ansible-control-key.yml \
  --vault-password-file /etc/grayhaven/ansible/secrets/vault-password \
  --extra-vars @/home/ansible/grayhaven-vault/vault/bastion.yml
```

After rotation, verify the active control bastion can SSH to managed hosts as
the `ansible` user and run a full convergence pass.

[Back to top](#operations)

## Triggering a Manual Backup

Run the managed restic backup service on the host whose backup should be
created:

```bash
sudo systemctl start grayhaven-restic-backup.service
```

Review the service result and recent backup log output:

```bash
sudo systemctl status grayhaven-restic-backup.service --no-pager
sudo journalctl -u grayhaven-restic-backup.service -n 100 --no-pager
```

When remote GCS backups are enabled, the same managed service writes to the
local restic repository first and then to the remote restic repository.

[Back to top](#operations)

## Verifying Local Backups

List local restic snapshots on the managed host:

```bash
sudo restic \
  --repo /var/backups/restic \
  --password-file /etc/grayhaven/backup/restic-password \
  snapshots --latest 1
```

To see more snapshot history, omit `--latest 1`:

```bash
sudo restic \
  --repo /var/backups/restic \
  --password-file /etc/grayhaven/backup/restic-password \
  snapshots
```

For a repository health check, run:

```bash
sudo restic \
  --repo /var/backups/restic \
  --password-file /etc/grayhaven/backup/restic-password \
  check
```

[Back to top](#operations)

## Verifying Remote Backups

Remote backup verification requires GCS remote backups to be enabled for the
host. The commands use the managed root-readable GCS credential file without
printing its contents.

```bash
GCS_PROJECT_ID="grayhaven"
REMOTE_REPOSITORY="gs:$(hostname -s)-restic:/"

sudo env \
  GOOGLE_PROJECT_ID="$GCS_PROJECT_ID" \
  GOOGLE_APPLICATION_CREDENTIALS=/etc/grayhaven/backup/gcs-credentials.json \
  restic \
    --repo "$REMOTE_REPOSITORY" \
    --password-file /etc/grayhaven/backup/restic-password \
    snapshots --latest 1
```

To see more remote snapshot history, omit `--latest 1`:

```bash
GCS_PROJECT_ID="grayhaven"
REMOTE_REPOSITORY="gs:$(hostname -s)-restic:/"

sudo env \
  GOOGLE_PROJECT_ID="$GCS_PROJECT_ID" \
  GOOGLE_APPLICATION_CREDENTIALS=/etc/grayhaven/backup/gcs-credentials.json \
  restic \
    --repo "$REMOTE_REPOSITORY" \
    --password-file /etc/grayhaven/backup/restic-password \
    snapshots
```

For a remote repository health check, run:

```bash
GCS_PROJECT_ID="grayhaven"
REMOTE_REPOSITORY="gs:$(hostname -s)-restic:/"

sudo env \
  GOOGLE_PROJECT_ID="$GCS_PROJECT_ID" \
  GOOGLE_APPLICATION_CREDENTIALS=/etc/grayhaven/backup/gcs-credentials.json \
  restic \
    --repo "$REMOTE_REPOSITORY" \
    --password-file /etc/grayhaven/backup/restic-password \
    check
```

[Back to top](#operations)

## Listing the Contents of a Local Backup

List the contents of the latest local snapshot:

```bash
sudo restic \
  --repo /var/backups/restic \
  --password-file /etc/grayhaven/backup/restic-password \
  ls latest
```

To inspect a specific snapshot, replace `<snapshot-id>` with an ID shown by
[Verifying Local Backups](#verifying-local-backups):

```bash
sudo restic \
  --repo /var/backups/restic \
  --password-file /etc/grayhaven/backup/restic-password \
  ls <snapshot-id>
```

[Back to top](#operations)

## Listing the Contents of a Remote Backup

List the contents of the latest remote snapshot:

```bash
GCS_PROJECT_ID="grayhaven"
REMOTE_REPOSITORY="gs:$(hostname -s)-restic:/"

sudo env \
  GOOGLE_PROJECT_ID="$GCS_PROJECT_ID" \
  GOOGLE_APPLICATION_CREDENTIALS=/etc/grayhaven/backup/gcs-credentials.json \
  restic \
    --repo "$REMOTE_REPOSITORY" \
    --password-file /etc/grayhaven/backup/restic-password \
    ls latest
```

To inspect a specific snapshot, replace `<snapshot-id>` with an ID shown by
[Verifying Remote Backups](#verifying-remote-backups):

```bash
GCS_PROJECT_ID="grayhaven"
REMOTE_REPOSITORY="gs:$(hostname -s)-restic:/"

sudo env \
  GOOGLE_PROJECT_ID="$GCS_PROJECT_ID" \
  GOOGLE_APPLICATION_CREDENTIALS=/etc/grayhaven/backup/gcs-credentials.json \
  restic \
    --repo "$REMOTE_REPOSITORY" \
    --password-file /etc/grayhaven/backup/restic-password \
    ls <snapshot-id>
```

[Back to top](#operations)

## Restoring a Local Backup

Restore into a temporary directory first. Do not restore directly over live
paths during validation or routine recovery.

Set `RESTORE_PATH` to the absolute path that should be restored:

```bash
RESTORE_PATH="/absolute/path/to/restore"
LOCAL_RESTORE_DIR=/tmp/grayhaven-restore-local
sudo rm -rf "$LOCAL_RESTORE_DIR"

sudo restic \
  --repo /var/backups/restic \
  --password-file /etc/grayhaven/backup/restic-password \
  restore latest \
  --target "$LOCAL_RESTORE_DIR" \
  --include "$RESTORE_PATH"
```

Review the restored data under `$LOCAL_RESTORE_DIR`, then copy back only the
intended path:

```bash
sudo cp -a "$LOCAL_RESTORE_DIR$RESTORE_PATH" "$(dirname "$RESTORE_PATH")/"
```

After validation or recovery is complete, remove the temporary restore
directory:

```bash
sudo rm -rf "$LOCAL_RESTORE_DIR"
```

If the restore fails, keep the backup service logs and restore directory state
intact until the failure is understood.

[Back to top](#operations)

## Restoring a Remote Backup

Remote restores require GCS remote backups to be enabled for the host. Restore
into a temporary directory first. Do not restore directly over live paths during
validation or routine recovery.

Set `RESTORE_PATH` to the absolute path that should be restored:

```bash
GCS_PROJECT_ID="grayhaven"
REMOTE_REPOSITORY="gs:$(hostname -s)-restic:/"
RESTORE_PATH="/absolute/path/to/restore"
REMOTE_RESTORE_DIR=/tmp/grayhaven-restore-remote
sudo rm -rf "$REMOTE_RESTORE_DIR"

sudo env \
  GOOGLE_PROJECT_ID="$GCS_PROJECT_ID" \
  GOOGLE_APPLICATION_CREDENTIALS=/etc/grayhaven/backup/gcs-credentials.json \
  restic \
    --repo "$REMOTE_REPOSITORY" \
    --password-file /etc/grayhaven/backup/restic-password \
    restore latest \
    --target "$REMOTE_RESTORE_DIR" \
    --include "$RESTORE_PATH"
```

Review the restored data under `$REMOTE_RESTORE_DIR`, then copy back only the
intended path:

```bash
sudo cp -a "$REMOTE_RESTORE_DIR$RESTORE_PATH" "$(dirname "$RESTORE_PATH")/"
```

After validation or recovery is complete, remove the temporary restore
directory:

```bash
sudo rm -rf "$REMOTE_RESTORE_DIR"
```

If the restore fails, keep the backup service logs and restore directory state
intact until the failure is understood.

[Back to top](#operations)

## Root Command Audit Trail

Managed sudo-capable users are covered by sudo journal logging and interactive
root-shell command logging. This records one-shot `sudo` commands and commands
typed in interactive root Bash shells reached through `sudo su -` or `su -`.
Command output is not captured. Use the Loki or journal timestamp as the event
timestamp.

The primary review path is Grafana Cloud Loki when log shipping is enabled.
Useful starting queries:

```logql
{job="systemd_journal"} |= "COMMAND=" != " ansible :"
{job="systemd_journal"} |= "grayhaven-root-command"
```

For a local fallback on a managed host, run:

```bash
sudo journalctl --since today -t sudo -t grayhaven-root-command --no-pager \
  | grep -E 'COMMAND=|grayhaven-root-command' \
  | grep -v ' ansible :'
```

Avoid placing secrets directly on command lines. If a secret is accidentally
exposed through command arguments, rotate the affected secret immediately.

[Back to top](#operations)

## Operator Tmux Console

On bastions, sudo-capable managed users receive the shared tmux configuration
and `gtmux` launcher. Run this command to attach to the standard operator
session, creating it first if needed:

```bash
gtmux
```

To reset and recreate the session from the configured workspace file, run this
from a normal shell outside tmux:

```bash
gtmux --reset
```

If tmux auto-attach is enabled for a user, logging in to that bastion user over
interactive SSH runs `gtmux` automatically. If that causes login trouble,
bypass it for one SSH session:

```bash
GRAYHAVEN_TMUX_AUTO_ATTACH_BYPASS=1 ssh <user>@bastion.grayhavensystems.com
```

The
[operator tmux architecture](operator-tmux-architecture.md)
documentation explains how workspace files are built and loaded. The public
[grayhaven-vault-example](https://github.com/dean1012/grayhaven-vault-example)
repository documents the
[managed user schema](https://github.com/dean1012/grayhaven-vault-example/blob/main/docs/schema.md#managed-user-entries)
for enabling auto-attach and selecting workspace files.

[Back to top](#operations)

## Grafana Cloud Observability

Grafana Cloud observability is configured by normal convergence when enabled
for production. To apply observability changes, run the normal manual runner
from the active control bastion:

```bash
sudo systemctl start grayhaven-ansible-runner.service
```

Useful local status commands:

```bash
sudo systemctl status alloy.service
sudo systemctl status grayhaven-observability-textfile.timer
sudo journalctl -u alloy.service
sudo journalctl -u grayhaven-ansible-runner.service
```

Ansible-managed Grafana alert rules are labeled `configured_by=ansible`.
Manual Grafana Cloud alert rules should not use that label because Ansible uses
it to identify rules it owns.

Grafana Cloud integration is supported only for the `prod` environment. The
[observability architecture](observability-architecture.md) documentation
describes what Ansible manages and what remains manual in Grafana Cloud.

[Back to top](#operations)

## Infrastructure Policy Changes

Active control bastion selection and web TLS mode are defined in
[`grayhaven-infra-opentofu`](https://github.com/dean1012/grayhaven-infra-opentofu)
policy. Changes to these settings must be applied in the
`grayhaven-infra-opentofu` repository. After applying the target workspace, run
a manual configuration pass from the active control bastion.

See the
[bastion failover documentation](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/operations.md#bastion-failover)
and
[TLS mode documentation](https://github.com/dean1012/grayhaven-infra-opentofu/blob/main/docs/operations.md#updating-workspace-environment-tls-mode)
in the
`grayhaven-infra-opentofu`
repository.

[Back to top](#operations)
