#!/usr/bin/env python3
"""Validate the production stack without Docker.

`docker compose -f infra/docker-compose.prod.yml config` is the real check, but CI runners and dev
laptops do not always have a Docker daemon. This script covers the failures that actually happen when
someone edits the file by hand:

* YAML that does not parse (a tab, an unbalanced quote);
* an anchor (`*backend-env`, `*logging`) that no longer exists after a rename;
* a `${VAR}` the stack references but `infra/.env.prod.example` never mentions — the deploy then fails at
  `up`, not at review;
* a required `${VAR:?…}` that is not in the example file, which is the same mistake with a worse error;
* a service with no restart policy, no logging caps or no healthcheck (other than the one-shot migrate);
* a database/redis that publishes a host port in production.

Exit code 1 on any problem, so it can be wired into CI. Usage:
    python infra/validate_prod_stack.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
COMPOSE = HERE / "docker-compose.prod.yml"
ENV_EXAMPLE = HERE / ".env.prod.example"

#: Services allowed to skip a healthcheck/restart policy.
ONESHOT = {"migrate"}
#: Services that must never publish a host port in production.
PRIVATE = {"postgres", "redis", "backend", "worker", "beat"}

VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(:[?\-])?")


def main() -> int:
    problems: list[str] = []
    text = COMPOSE.read_text(encoding="utf-8")
    try:
        stack = yaml.safe_load(text)
    except yaml.YAMLError as error:
        print(f"✗ {COMPOSE.name} does not parse: {error}")
        return 1

    if not isinstance(stack, dict) or "services" not in stack:
        print("✗ no `services:` mapping — is this a compose file?")
        return 1

    # 1. anchors referenced by name must exist somewhere in the document
    anchors = set(re.findall(r"&([A-Za-z_][\w-]*)", text))
    # an alias is a value on its own (`*backend-env`), never part of a quoted string like `celery.*beat`
    aliases = set(re.findall(r"(?<![\w.*])\*([A-Za-z_][\w-]*)", text))
    for alias in aliases:
        if alias not in anchors:
            problems.append(f"alias *{alias} has no matching &anchor")

    # 2. every variable the stack reads must be documented in the example env file
    if not ENV_EXAMPLE.exists():
        problems.append(f"{ENV_EXAMPLE.name} is missing")
        documented: set[str] = set()
    else:
        example = ENV_EXAMPLE.read_text(encoding="utf-8")
        documented = set(re.findall(r"^([A-Z0-9_]+)=", example, flags=re.MULTILINE))

    referenced = {match.group(1) for match in VAR_PATTERN.finditer(text)}
    # nginx/docker built-ins and values computed inside the stack are not operator inputs
    internal = {"HOSTNAME", "POSTGRES_USER", "POSTGRES_DB"}
    undocumented = sorted(referenced - documented - internal)
    if undocumented:
        problems.append("referenced in the compose but undocumented in .env.prod.example: " + ", ".join(undocumented))

    required = {match.group(1) for match in VAR_PATTERN.finditer(text) if match.group(2) == ":?"}
    missing_required = sorted(required - documented)
    if missing_required:
        problems.append("required (${VAR:?}) but absent from .env.prod.example: " + ", ".join(missing_required))

    # 3. per-service operational settings
    services = stack["services"]
    for name, service in services.items():
        if name in ONESHOT:
            continue
        if service.get("restart") != "unless-stopped":
            problems.append(f"{name}: restart must be `unless-stopped` (got {service.get('restart')!r})")
        if "logging" not in service:
            problems.append(f"{name}: no logging caps — an unbounded log file fills the disk")
        if "healthcheck" not in service:
            problems.append(f"{name}: no healthcheck — the container is 'up' even when it cannot serve")
        if name in PRIVATE and service.get("ports"):
            problems.append(f"{name}: publishes host port(s) {service['ports']} in production")

    for name in ("postgres", "redis"):
        if name not in services:
            problems.append(f"{name} service is missing")

    if problems:
        print(f"✗ {len(problems)} problem(s) in {COMPOSE.name}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"✓ {COMPOSE.name}: {len(services)} services, {len(referenced)} variables, all documented")
    for name in services:
        print(f"    · {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
