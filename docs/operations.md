# Operations

## Deployment Flow

The production deployment flow starts in the OpenTofu repository:

```bash
tofu fmt -recursive
tofu validate
tofu plan
tofu apply
```

OpenTofu provisions the droplets and cloud-init runs this repository's
bootstrap playbook on first boot. The bastion bootstrap then starts the
full-playbook runner once immediately and enables a daily timer.

## Bastion Runner

Bootstrap installs the runner on bastion hosts only:

- executable: `/usr/local/sbin/grayhaven-ansible-runner`
- service: `/etc/systemd/system/grayhaven-ansible-runner.service`
- timer: `/etc/systemd/system/grayhaven-ansible-runner.timer`
- checkout: `/home/ansible/grayhaven-config-ansible`

The runner logs to systemd journal output. Useful commands on the bastion host:

```bash
systemctl status grayhaven-ansible-runner.service
systemctl status grayhaven-ansible-runner.timer
journalctl -u grayhaven-ansible-runner.service
```

## Validation Targets

After deployment, confirm:

- bootstrap completed on all droplets;
- the runner service and timer exist only on bastion hosts;
- the runner can rerun without changing already-converged resources;
- `jsmith` exists on all managed hosts;
- SSH reaches bastion as `jsmith`;
- SSH reaches other managed hosts through bastion over private IPv4.

## Local Repository Checks

Run these checks before opening a pull request:

```bash
yamllint .
ansible-lint .
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/bootstrap.yml
ansible-playbook -i localhost, --connection=local --syntax-check playbooks/site.yml
markdownlint-cli2 '**/*.md'
```
