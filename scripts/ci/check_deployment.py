"""Offline checks for the prepared single-VPS deployment configuration.

This script reads YAML and source files, parses embedded Python, syntax-checks
shell scripts, and simulates fail-closed guards in a temporary repository copy.
It never imports the application, opens a database, contacts a network, or
starts a real Compose project. Optional Nginx checks use temporary configs and
temporary self-signed certificates only.
"""

from __future__ import annotations

import argparse
import ast
import base64
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlunsplit

try:
    import yaml
except ImportError as exc:  # pragma: no cover - depends on the selected tooling profile
    raise SystemExit("PyYAML is required (install the pinned .ci/requirements.txt profile).") from exc


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy.yml"
PASS_COUNT = 0


def require(condition: bool, message: str) -> None:
    global PASS_COUNT
    if not condition:
        raise ValueError(message)
    PASS_COUNT += 1


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> dict:
    value = yaml.load(read(path), Loader=yaml.BaseLoader)
    require(isinstance(value, dict), f"YAML document must be a mapping: {path.relative_to(ROOT)}")
    return value


def list_value(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def compose_contract() -> tuple[dict, dict]:
    production = load_yaml(ROOT / "docker/docker-compose.production.yml")
    staging = load_yaml(ROOT / "docker/docker-compose.staging.yml")
    prod_services = production.get("services", {})
    stage_services = staging.get("services", {})
    require({"db", "redis", "api", "nginx", "runtime"} <= prod_services.keys(), "Production services missing")
    require({"db", "redis", "api", "nginx"} == stage_services.keys(), "Staging must not define a runtime service")

    prod_volumes = set(production.get("volumes", {}))
    stage_volumes = set(staging.get("volumes", {}))
    require(prod_volumes == {"production_pgdata"}, "Production needs its own named database volume")
    require(stage_volumes == {"staging_pgdata"}, "Staging needs its own named database volume")
    require(prod_volumes.isdisjoint(stage_volumes), "Production and staging volumes must be distinct")
    require(
        all((config or {}).get("external") != "true" for config in production.get("volumes", {}).values()),
        "Production database volume must be privately owned by its Compose project",
    )
    require(
        all((config or {}).get("external") != "true" for config in staging.get("volumes", {}).values()),
        "Staging database volume must be privately owned by its Compose project",
    )

    prod_env = list_value(prod_services["api"].get("env_file"))
    stage_env = list_value(stage_services["api"].get("env_file"))
    require(prod_env == ["../.env.production"], "Production API must use only .env.production")
    require(stage_env == ["../.env.staging"], "Staging API must use only .env.staging")
    require(prod_env != stage_env, "Deployment env files must be distinct")
    require(list_value(prod_services["runtime"].get("env_file")) == prod_env, "Runtime env must match production")

    stage_networks = staging.get("networks", {})
    require(
        bool(stage_networks) and all(net.get("internal") == "true" for net in stage_networks.values()),
        "Every staging network must be internal/no-egress",
    )
    profiles = list_value(prod_services["runtime"].get("profiles"))
    require(profiles == ["runtime"], "Production must have one opt-in runtime profile")
    require(
        prod_services["runtime"].get("command") == ["python", "-m", "app.runtime"],
        "Production runtime must have the canonical app.runtime command",
    )

    published: dict[str, list[str]] = {"production": [], "staging": []}
    for name, services, target in (
        ("production", prod_services, published["production"]),
        ("staging", stage_services, published["staging"]),
    ):
        for service, config in services.items():
            for port in list_value(config.get("ports")):
                require(
                    isinstance(port, str) and port.startswith("127.0.0.1:"),
                    f"{name}.{service} publishes a non-loopback port: {port!r}",
                )
                target.append(port)
    require(published["production"] == ["127.0.0.1:8226:80"], "Production must expose only its loopback proxy")
    require("127.0.0.1:18000:80" in published["staging"], "Staging proxy must use its own loopback port")
    require("127.0.0.1:55432:5432" in published["staging"], "Staging DB port must be loopback-only")
    require(
        not any(port.endswith(":5432") for port in published["production"]),
        "Production PostgreSQL must not publish a host port",
    )

    for name, services in (("production", prod_services), ("staging", stage_services)):
        api_command = " ".join(list_value(services["api"].get("command")))
        require(
            "uvicorn" in api_command and "alembic" not in api_command, f"{name} API startup must not run migrations"
        )
        require(
            list_value(services["api"].get("expose")) == ["8000"], f"{name} API must expose only its internal HTTP port"
        )

    for name in ("production", "staging"):
        project = "social-ai-production" if name == "production" else "social-ai-staging"
        wrapper = ROOT / "scripts/ops" / f"{name}_compose.sh"
        assignments = shell_assignments(wrapper)
        require(
            assignments.get("ENV_FILE", "").endswith(f".env.{name}"), f"{name} wrapper must select its own env file"
        )
        lines = (
            shlex.split(line)
            for line in logical_shell_lines(read(wrapper))
            if line.lstrip().startswith("exec docker compose")
        )
        require(
            any(
                tokens[index : index + 2] == ["--project-name", project]
                for tokens in lines
                for index in range(max(0, len(tokens) - 1))
            ),
            f"{name} wrapper must select project {project}",
        )
    return production, staging


def build_context(compose_path: Path, config: dict) -> Path:
    build = config.get("build", {})
    context = Path(build.get("context", "."))
    if not context.is_absolute():
        context = compose_path.parent / context
    return context.resolve()


def dockerfile_for(compose_path: Path, config: dict) -> tuple[Path, Path]:
    context = build_context(compose_path, config)
    dockerfile_name = config.get("build", {}).get("dockerfile", "Dockerfile")
    dockerfile = Path(dockerfile_name)
    if not dockerfile.is_absolute():
        dockerfile = context / dockerfile
    return context, dockerfile.resolve()


def shell_assignments(path: Path) -> dict[str, str]:
    assignments = {}
    for line in read(path).splitlines():
        match = re.match(r"^([A-Z_]+)=\"?([^\";]+)\"?$", line.strip())
        if match:
            assignments[match.group(1)] = match.group(2)
    return assignments


def logical_shell_lines(source: str):
    pending = ""
    for line in source.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
        else:
            yield pending + line
            pending = ""
    if pending:
        yield pending


def dockerfile_contract(compose: dict, name: str) -> None:
    compose_path = ROOT / f"docker/docker-compose.{name}.yml"
    services = compose["services"]
    app_build = services["api"].get("build")
    if name == "production":
        require(app_build is None, "Production API must use the published immutable image, not a local build")
        image = services["api"].get("image", "")
        require(
            "${APP_IMAGE:?" in image and "immutable GHCR" in image,
            "Production API must require APP_IMAGE as its immutable GHCR image",
        )
        check_dockerfile_sources(ROOT / "docker/Dockerfile", ROOT.resolve())
    else:
        context, app_dockerfile = dockerfile_for(compose_path, services["api"])
        require(context == ROOT.resolve(), f"{name} application build context must be repository root")
        require(
            app_dockerfile == (ROOT / "docker/Dockerfile").resolve() and app_dockerfile.is_file(),
            f"{name} application Dockerfile path does not resolve from its actual build context",
        )
        check_dockerfile_sources(app_dockerfile, context)

    nginx_context, nginx_dockerfile = dockerfile_for(compose_path, services["nginx"])
    require(nginx_context == (ROOT / "nginx").resolve(), f"{name} proxy build context must resolve to nginx/")
    require(
        nginx_dockerfile == (ROOT / "nginx/Dockerfile").resolve() and nginx_dockerfile.is_file(),
        f"{name} proxy Dockerfile path does not resolve from its actual build context",
    )
    check_dockerfile_sources(nginx_dockerfile, nginx_context)


def check_dockerfile_sources(dockerfile: Path, context: Path) -> None:
    for number, line in enumerate(read(dockerfile).splitlines(), start=1):
        parts = line.strip().split()
        if not parts or parts[0].upper() != "COPY":
            continue
        sources = [part for part in parts[1:] if not part.startswith("--")][:-1]
        require(bool(sources), f"COPY has no source at {dockerfile}:{number}")
        for source in sources:
            if "*" in source or "?" in source or "[" in source:
                matches = list(context.glob(source))
                require(bool(matches), f"COPY wildcard has no build-context matches at {dockerfile}:{number}")
            else:
                path = (context / source).resolve()
                require(
                    path == context or context in path.parents,
                    f"COPY escapes build context at {dockerfile}:{number}",
                )
                require(path.exists(), f"COPY source is missing from build context at {dockerfile}:{number}: {source}")


def workflow_contract() -> None:
    workflow = load_yaml(WORKFLOW)
    events = workflow.get("on", {})  # BaseLoader keeps YAML 1.1's `on` as a string.
    require(set(events) == {"pull_request", "push"}, "Workflow must be limited to PR validation and branch pushes")
    require(
        list_value(events["pull_request"].get("branches")) == ["dev", "master"],
        "PR validation branch filters must be dev/master",
    )
    require(list_value(events["push"].get("branches")) == ["dev", "master"], "Push branch filters must be dev/master")

    jobs = workflow.get("jobs", {})
    deploy = jobs.get("deploy", {})
    build = jobs.get("build-and-push", {})
    pr_job = jobs.get("validate-pr", {})
    require("github.event_name == 'pull_request'" in pr_job.get("if", ""), "PR builds must stay in validation-only job")
    require("push: false" in read(WORKFLOW), "PR validation must not publish images")
    require("github.event_name == 'push'" in build.get("if", ""), "Only pushes may publish images")
    deploy_if = deploy.get("if", "")
    require(
        "refs/heads/dev" in deploy_if and "STAGING_DEPLOY_ENABLED" in deploy_if,
        "Staging deploy must be explicitly gated to dev",
    )
    require(
        "refs/heads/master" in deploy_if and "PRODUCTION_DEPLOY_ENABLED" in deploy_if,
        "Production deploy must be explicitly gated to master",
    )
    require(
        "PRODUCTION_BACKUP_READY" in deploy_if and "PRODUCTION_MIGRATIONS_APPROVED_SHA" in deploy_if,
        "Production deploy must require backup readiness and exact-SHA migration approval",
    )
    environment = deploy.get("environment", {}).get("name", "")
    require(
        "production" in environment and "staging" in environment, "Deploys must use branch-scoped GitHub environments"
    )
    require(
        "needs.build-and-push.outputs.image_digest" in deploy.get("env", {}).get("IMAGE_REF", ""),
        "Deployment must use the registry digest output",
    )
    require("@${{" in deploy.get("env", {}).get("IMAGE_REF", ""), "Deployment image ref must be digest-pinned")
    require("docker/setup-buildx-action" in read(WORKFLOW), "Buildx must be configured")
    require("cache-to: type=gha,mode=max" in read(WORKFLOW), "Build cache export must be configured")
    require("file: ./docker/Dockerfile" in read(WORKFLOW), "Workflow must build the checked-in Dockerfile")
    require(
        ":sha-${{ github.sha }}" in workflow.get("env", {}).get("IMAGE_TAG", ""),
        "Published image tag must be commit-pinned",
    )
    deploy_script = read(ROOT / "scripts/ops/deploy_vps.sh")
    normalized_deploy = re.sub(r"\s+", " ", deploy_script)
    require(
        re.search(r"PRODUCTION_MIGRATIONS_APPROVED_IMAGE:-.*!=.*\$image", normalized_deploy) is not None,
        "Production deploy script must bind migration approval to the exact image digest",
    )

    for target in ("staging", "production"):
        body = shell_case_body(deploy_script, target)
        commands = []
        for line in logical_shell_lines(body):
            if line.lstrip().startswith('bash "$compose"') or "backup_production_db.sh" in line:
                commands.append(shlex.split(line))

        def command_at(predicate) -> int:
            return next((index for index, command in enumerate(commands) if predicate(command)), -1)

        stop_at = command_at(lambda command: command == ["bash", "$compose", "stop", "api"])
        migrate_at = command_at(
            lambda command: command[:5] == ["bash", "$compose", "run", "--rm", "--no-deps"]
            and command[-3:] == ["alembic", "upgrade", "head"]
        )
        restart_at = command_at(
            lambda command: command[:3] == ["bash", "$compose", "up"]
            and "--force-recreate" in command
            and command[-1:] == ["api"]
        )
        require(
            -1 < stop_at < migrate_at < restart_at,
            f"{target} deployment must stop API, run one migration, then replace API",
        )
        require(
            sum(command.count("alembic") for command in commands) == 1, f"{target} deployment must have one migrator"
        )
        if target == "production":
            backup_at = command_at(lambda command: any("backup_production_db.sh" in token for token in command))
            require(backup_at >= 0 and backup_at < stop_at, "Production must snapshot before API maintenance")


def shell_case_body(script: str, target: str) -> str:
    pattern = rf"(?ms)^[ \t]*{re.escape(target)}\)[ \t]*\n(.*?)^[ \t]*;;[ \t]*$"
    match = re.search(pattern, script)
    require(match is not None, f"Could not locate the {target} case body in deploy script")
    return match.group(1)


def embedded_python_and_shell() -> None:
    ops = sorted((ROOT / "scripts/ops").glob("*.sh"))
    for path in ops:
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        require(result.returncode == 0, f"bash -n failed for {path.name}: {result.stderr.strip()}")
    scripts = [*ops, WORKFLOW]
    heredoc = re.compile(r"python3\s+[^\n]*<<['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\n(.*?)^\1\s*$", re.M | re.S)
    for path in scripts:
        content = read(path)
        for match in heredoc.finditer(content):
            compile(ast.parse(match.group(2)), str(path), "exec")
            require(True, f"embedded Python parsed in {path.name}")


def nginx_static_contract() -> None:
    proxy = read(ROOT / "nginx/default.conf")
    host = read(ROOT / "nginx/vps-sites.conf.example")
    require("server api:8000;" in proxy, "Container Nginx must proxy to FastAPI's api service")
    require(
        "server_name app.example.invalid stage.example.invalid;" in host,
        "Host Nginx sample must use domain placeholders",
    )
    require("return 301 https://$host$request_uri;" in host, "Host Nginx must redirect HTTP to HTTPS")
    require(
        "proxy_pass http://127.0.0.1:8226;" in host and "proxy_pass http://127.0.0.1:18000;" in host,
        "Production and staging vhosts must use their dedicated loopback ingress ports",
    )
    require(
        "REPLACE_WITH_CERT_NAME" in host and "example.invalid" in host,
        "Host Nginx sample must not embed live domains or certificates",
    )


def env_text(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def database_url(user: str, password: str, host: str, name: str) -> str:
    return urlunsplit(("postgresql+asyncpg", f"{user}:{password}@{host}:5432", f"/{name}", "", ""))


def staging_env(password: str | None = None, database_host: str = "db", placeholders: bool = False) -> str:
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    signing_key = "replace-with-test-placeholder" if placeholders else secrets.token_urlsafe(48)
    db_password = "replace-with-test-placeholder" if placeholders else (password or secrets.token_hex(32))
    values = {
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "SECRET_KEY": signing_key,
        "CREDENTIALS_KEY": key,
        "BACKEND_CORS_ORIGINS": "[]",
        "POSTGRES_USER": "staging",
        "POSTGRES_PASSWORD": db_password,
        "POSTGRES_DB": "social_ai_staging",
        "POSTGRES_URL": database_url("staging", db_password, database_host, "social_ai_staging"),
        "DB_SCHEMA": "public",
        "DB_POOL": "pool",
        "REDIS_URL": "redis://redis:6379/0",
        "DOCKER_SUBNET": "172.30.43.0/24",
        "NGINX_CONTAINER_IP": "172.30.43.2",
        "SCHEDULER_ENABLED": "false",
        "DATA_RETENTION_MONTHS": "1",
    }
    return env_text(values)


def production_env(database_host: str = "db") -> str:
    password = secrets.token_hex(32)
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    values = {
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "CREDENTIALS_KEY": key,
        "BACKEND_CORS_ORIGINS": "[]",
        "ADMIN_ENABLED": "true",
        "POSTGRES_USER": "social_ai",
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DB": "social_ai",
        "POSTGRES_URL": database_url("social_ai", password, database_host, "social_ai"),
        "DB_SCHEMA": "public",
        "DB_POOL": "pool",
        "REDIS_URL": "redis://redis:6379/0",
        "DOCKER_SUBNET": "172.30.42.0/24",
        "NGINX_CONTAINER_IP": "172.30.42.2",
        "SCHEDULER_ENABLED": "true",
        "DATA_RETENTION_MONTHS": "1",
    }
    return env_text(values)


def compose_parser_check() -> None:
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("--compose requires the Docker CLI with the Compose v2 plugin")
    with tempfile.TemporaryDirectory(prefix="social-ai-compose-check-") as temporary:
        scratch = Path(temporary) / "repo"
        (scratch / "docker").mkdir(parents=True)
        (scratch / "nginx").mkdir(parents=True)
        for name in ("docker-compose.production.yml", "docker-compose.staging.yml", "Dockerfile"):
            shutil.copy2(ROOT / "docker" / name, scratch / "docker" / name)
        for name in ("Dockerfile", "default.conf"):
            shutil.copy2(ROOT / "nginx" / name, scratch / "nginx" / name)
        digest_image = "ghcr.io/starck43/social-media-ai@sha256:" + "a" * 64
        for target in ("production", "staging"):
            env_path = scratch / f".env.{target}"
            env_path.write_text(production_env() if target == "production" else staging_env(), encoding="utf-8")
            env_path.chmod(0o600)
            env = os.environ.copy()
            if target == "production":
                env["APP_IMAGE"] = digest_image
            command = [
                docker,
                "compose",
                "--project-name",
                f"social-ai-{target}",
                "--env-file",
                str(env_path),
                "--file",
                str(scratch / "docker" / f"docker-compose.{target}.yml"),
                "config",
                "--quiet",
            ]
            result = subprocess.run(command, cwd=scratch, env=env, capture_output=True, text=True, check=False)
            require(
                result.returncode == 0,
                f"Docker Compose config parse failed for {target} (exit {result.returncode}; output suppressed)",
            )


def run_guard(script: Path, root: Path, docker_path: Path, log: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"{docker_path}{os.pathsep}{env.get('PATH', '')}"
    env["DOCKER_GUARD_LOG"] = str(log)
    env["XDG_STATE_HOME"] = str(root / ".synthetic-state")
    env.pop("APP_IMAGE", None)
    env.pop("PRODUCTION_MIGRATIONS_APPROVED_IMAGE", None)
    return subprocess.run(["bash", str(script), *args], cwd=root, env=env, capture_output=True, text=True, check=False)


def guard_simulations() -> None:
    with tempfile.TemporaryDirectory(prefix="social-ai-deployment-check-") as temporary:
        temp = Path(temporary)
        repo = temp / "repo"
        (repo / "scripts/ops").mkdir(parents=True)
        (repo / "docker").mkdir()
        for name in ("staging_compose.sh", "production_compose.sh", "restore_staging_db.sh", "deploy_vps.sh"):
            shutil.copy2(ROOT / "scripts/ops" / name, repo / "scripts/ops" / name)
        (repo / "docker/docker-compose.staging.yml").write_text("services: {}\n", encoding="utf-8")
        docker_bin = temp / "bin"
        docker_bin.mkdir()
        docker_log = temp / "docker-invoked"
        docker = docker_bin / "docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_GUARD_LOG"\nexit 0\n', encoding="utf-8")
        docker.chmod(0o700)
        env_file = repo / ".env.staging"
        wrapper = repo / "scripts/ops/staging_compose.sh"

        result = run_guard(wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(result.returncode == 2 and not docker_log.exists(), "Missing staging env must reject before Docker")

        env_file.write_text(staging_env(), encoding="utf-8")
        env_file.chmod(0o644)
        result = run_guard(wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(
            result.returncode == 2 and not docker_log.exists(),
            "Insecure staging env mode must reject before Docker",
        )

        env_file.write_text(staging_env(placeholders=True), encoding="utf-8")
        env_file.chmod(0o600)
        result = run_guard(wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(result.returncode != 0 and not docker_log.exists(), "Staging placeholders must reject before Docker")

        password = secrets.token_hex(32)
        env_file.write_text(staging_env(password=password, database_host="localhost"), encoding="utf-8")
        env_file.chmod(0o600)
        result = run_guard(wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(result.returncode == 1 and not docker_log.exists(), "Invalid staging DB host must reject before Docker")

        env_file.write_text(staging_env(), encoding="utf-8")
        env_file.chmod(0o600)
        result = run_guard(wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(
            result.returncode == 0 and docker_log.exists(),
            "Valid staging env should reach only the mock Docker binary",
        )

        docker_log.unlink()
        archive = temp / "synthetic.dump"
        archive.write_bytes(b"synthetic archive data")
        (temp / "synthetic.dump.sha256").write_text(f"{'0' * 64}  synthetic.dump\n", encoding="utf-8")
        restore = repo / "scripts/ops/restore_staging_db.sh"
        result = run_guard(restore, repo, docker_bin, docker_log, str(archive))
        require(
            result.returncode == 2 and not docker_log.exists(),
            "Restore without explicit flags must reject before Docker",
        )
        result = run_guard(
            restore,
            repo,
            docker_bin,
            docker_log,
            str(archive),
            "--approved-sanitized-copy",
            "--confirm-overwrite-staging",
        )
        require(result.returncode == 2 and not docker_log.exists(), "Tampered checksum must reject before Docker")

        production_wrapper = repo / "scripts/ops/production_compose.sh"
        production_file = repo / ".env.production"
        result = run_guard(production_wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(result.returncode == 2 and not docker_log.exists(), "Missing production env must reject before Docker")
        production_file.write_text("invalid env\n", encoding="utf-8")
        production_file.chmod(0o644)
        result = run_guard(production_wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(result.returncode == 2 and not docker_log.exists(), "Insecure production env must reject before Docker")

        production_file.write_text(production_env(database_host="localhost"), encoding="utf-8")
        production_file.chmod(0o600)
        result = run_guard(production_wrapper, repo, docker_bin, docker_log, "config", "--quiet")
        require(
            result.returncode == 1 and not docker_log.exists(), "Invalid production DB host must reject before Docker"
        )

        deploy = repo / "scripts/ops/deploy_vps.sh"
        valid_digest = "ghcr.io/starck43/social-media-ai@sha256:" + secrets.token_hex(32)
        result = run_guard(deploy, repo, docker_bin, docker_log, "staging", "ghcr.io/starck43/social-media-ai:latest")
        require(result.returncode == 2 and not docker_log.exists(), "Mutable image tag must reject before Docker")
        result = run_guard(deploy, repo, docker_bin, docker_log, "production", valid_digest)
        require(
            result.returncode == 2 and not docker_log.exists(),
            "Production deploy without exact-image migration approval must reject before Docker",
        )


def nginx_syntax(binary: str) -> None:
    with tempfile.TemporaryDirectory(prefix="social-ai-nginx-check-") as temporary:
        temp = Path(temporary)
        cert = temp / "selfsigned.pem"
        key = temp / "selfsigned.key"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=example.invalid",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        internal = read(ROOT / "nginx/default.conf").replace("server api:8000;", "server 127.0.0.1:8000;")
        host = read(ROOT / "nginx/vps-sites.conf.example")
        host = host.replace("/etc/letsencrypt/live/REPLACE_WITH_CERT_NAME/fullchain.pem", str(cert))
        host = host.replace("/etc/letsencrypt/live/REPLACE_WITH_CERT_NAME/privkey.pem", str(key))
        host = re.sub(r"^\s*include /etc/letsencrypt/options-ssl-nginx\.conf;\s*$", "", host, flags=re.M)
        host = re.sub(r"^\s*ssl_dhparam /etc/letsencrypt/ssl-dhparams\.pem;\s*$", "", host, flags=re.M)
        for name, source in (("container", internal), ("host", host)):
            config = temp / f"{name}.conf"
            config_text = (
                f"pid {temp / f'{name}.pid'};\n"
                f"error_log {temp / f'{name}.error.log'};\n"
                f"events {{}}\nhttp {{\n{source}\n}}\n"
            )
            config.write_text(config_text, encoding="utf-8")
            result = subprocess.run(
                [binary, "-t", "-e", "stderr", "-c", str(config), "-p", str(temp)],
                capture_output=True,
                text=True,
            )
            require(
                result.returncode == 0,
                f"Temporary adapted {name} Nginx config failed syntax check: {result.stderr.strip()}",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", action="store_true", help="syntax-check adapted proxy/vhost configs with nginx -t")
    parser.add_argument(
        "--compose",
        action="store_true",
        help="run Docker Compose config --quiet with synthetic scratch env files",
    )
    args = parser.parse_args()
    try:
        production, staging = compose_contract()
        dockerfile_contract(production, "production")
        dockerfile_contract(staging, "staging")
        workflow_contract()
        embedded_python_and_shell()
        nginx_static_contract()
        guard_simulations()
        if args.nginx:
            nginx = shutil.which("nginx")
            openssl = shutil.which("openssl")
            require(bool(nginx and openssl), "--nginx requires nginx and openssl binaries")
            nginx_syntax(nginx)
        if args.compose:
            compose_parser_check()
        print(f"PASS: deployment contract checks ({PASS_COUNT} assertions)")
        if args.nginx:
            print("NOTE: Nginx syntax was checked from temporary adapted configs with a temporary certificate.")
        if args.compose:
            print("NOTE: Docker Compose config parsing used synthetic files only; no containers were started.")
        print("LIMIT: Guard execution used a mock Docker binary; no app imports, DB, network, or deployment ran.")
        return 0
    except (OSError, ValueError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
