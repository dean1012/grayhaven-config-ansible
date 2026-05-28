# Grayhaven Configuration (Ansible)

Configuration management repository for Grayhaven Systems LLC using Ansible.

## Overview

This repository contains Ansible playbooks, roles, inventory configuration, and
supporting automation used to configure and manage Grayhaven Systems LLC Linux
servers after infrastructure provisioning.

Current configuration goals include:

- First-boot server bootstrap automation
- Bastion-based administrative access model
- Role-based Linux server configuration
- Secure SSH user and key management
- Idempotent system configuration workflows
- GitHub Actions validation workflows
- Enterprise-oriented configuration management practices

## Repository Scope

This repository is intended for:

- Grayhaven Systems LLC infrastructure
- Personal infrastructure projects
- Public portfolio and educational reference

Client infrastructure, credentials, deployment data, private SSH keys, secrets,
and operational state are not stored in this repository.

## Planned Configuration

Initial planned configuration includes:

- Production server bootstrap automation
- Bastion and web server role configuration
- Administrative user and automation user management
- SSH access preparation for bastion-based management
- Local storage of runtime secrets provided during bootstrap where required
- Future scheduled Ansible convergence from the bastion host
- Future static web hosting with automated TLS certificate management
- CI validation workflows

## Requirements

- Ansible
- AlmaLinux 10 target servers
- DigitalOcean infrastructure provisioned separately
- SSH public key for administrative access
- Bootstrap variables supplied during first-boot provisioning

## Repository Structure

```text
docs/        Project and operational documentation
playbooks/   Grayhaven Systems LLC Ansible playbooks
```

## Status

Project initialization in progress.
