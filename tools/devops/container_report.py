"""Mede o que importa para otimizar um container: imagem, camadas e runtime.

Uso:
    python tools/devops/container_report.py                    # descobre pelo compose
    python tools/devops/container_report.py IMAGEM [CONTAINER]
    python tools/devops/container_report.py IMAGEM --json      # resumo legivel por maquina
    CONTAINER_RUNTIME=podman python tools/devops/container_report.py

Sem dependencias: usa apenas a CLI do docker/podman e a biblioteca padrao, para
rodar igual no Windows (Docker Desktop), no WSL e num servidor Linux.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT = 120
CGROUP_FILES = ("memory.current", "memory.peak", "memory.max", "pids.current")
CGROUP_STATS = ("anon", "file", "slab")


# ---------------------------------------------------------------------------
# Execucao das CLIs
# ---------------------------------------------------------------------------
def detect_runtime() -> str:
    """docker ou podman (CONTAINER_RUNTIME força um deles)."""
    forced = os.environ.get("CONTAINER_RUNTIME", "").strip()
    if forced:
        return forced
    for candidate in ("docker", "podman"):
        if shutil.which(candidate) and _run([candidate, "version", "--format", "{{.Server.Version}}"])[0] == 0:
            return candidate
    raise SystemExit("nenhum runtime de containers respondeu (docker/podman)")


def _run(command: list[str], *, timeout: int = TIMEOUT) -> tuple[int, str]:
    """Roda um comando; devolve (codigo, saida) sem estourar excecao."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"<erro: {exc}>"
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode, output


def _json(command: list[str]) -> object | None:
    """Parse JSON output; also accepts one JSON object per line (docker --format)."""
    code, output = _run(command)
    if code != 0 or not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        rows = []
        for text in output.splitlines():
            text = text.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError:
                return None
        return rows or None


# ---------------------------------------------------------------------------
# Formatacao
# ---------------------------------------------------------------------------
def human_bytes(value: float | int | None) -> str:
    if value is None:
        return "n/d"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def line(label: str, value: object) -> None:
    print(f"  {label:<34} {value}")


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------
def image_report(runtime: str, image: str) -> dict:
    section(f"IMAGEM  {image}")
    raw = _json([runtime, "image", "inspect", image, "--format", "{{json .}}"])
    if not isinstance(raw, dict):
        print(f"  imagem nao encontrada: {image}")
        return {}
    config = raw.get("Config") or {}
    layers = (raw.get("RootFS") or {}).get("Layers") or []
    report = {
        "size_bytes": raw.get("Size"),
        "layers": len(layers),
        "user": config.get("User") or "(root)",
        "workdir": config.get("WorkingDir") or "/",
        "env_vars": len(config.get("Env") or []),
        "has_healthcheck": bool(config.get("Healthcheck")),
    }
    line("tamanho (.Size)", human_bytes(report["size_bytes"]))
    listed = _json([runtime, "image", "ls", image, "--format", "{{json .}}"])
    if isinstance(listed, list) and listed:
        line("tamanho (docker images)", listed[0].get("Size", "n/d"))
    line("camadas", report["layers"])
    line("usuario", report["user"])
    line("workdir", report["workdir"])
    line("variaveis de ambiente", report["env_vars"])
    line("healthcheck", "sim" if report["has_healthcheck"] else "nao")
    line("criada em", str(raw.get("Created", "?"))[:19])

    history = _json([runtime, "history", image, "--no-trunc", "--format", "{{json .}}"])
    if isinstance(history, list):
        sized = sorted(
            ((_parse_size(item.get("Size")), item.get("CreatedBy", "")) for item in history),
            reverse=True,
        )
        biggest = [(size, created_by) for size, created_by in sized if size][:6]
        if biggest:
            print("\n  maiores camadas:")
            for size, created_by in biggest:
                print(f"    {human_bytes(size):>10}  {created_by[:70]}")
        else:
            print(
                "\n  maiores camadas: n/d\n"
                "    (o driver de armazenamento atual nao informa o tamanho por camada)"
            )
    return report


def contents_report(runtime: str, image: str) -> dict:
    """Diretorios do sistema de arquivos final (o que realmente vai no disco)."""
    section("CONTEUDO DA IMAGEM (maiores diretorios)")
    command = [
        runtime, "run", "--rm", "--network", "none", "--entrypoint", "sh", image,
        "-c", "du -xhd1 / 2>/dev/null | sort -h | tail -15",
    ]
    code, output = _run(command)
    if code != 0:
        print(f"  nao foi possivel inspecionar (imagem sem shell?): {output[:120]}")
        return {}
    print("  " + output.replace("\n", "\n  "))
    highlights: dict[str, int] = {}
    for row in output.splitlines():
        parts = row.split("\t", 1)
        if len(parts) == 2 and parts[1].strip() in ("/app", "/usr", "/opt"):
            highlights[parts[1].strip()] = _parse_size(parts[0])
    return highlights


def container_report(runtime: str, container: str) -> dict:
    section(f"RUNTIME  {container}")
    raw = _json([runtime, "inspect", container, "--format", "{{json .}}"])
    if not isinstance(raw, dict):
        print("  container nao encontrado (a imagem foi medida; suba o servico para medir RAM)")
        return {}
    state = raw.get("State") or {}
    host = raw.get("HostConfig") or {}
    report = {
        "status": state.get("Status"),
        "restart_policy": str((host.get("RestartPolicy") or {}).get("Name")),
        "memory_limit": host.get("Memory"),
        "writable_layer_bytes": raw.get("SizeRw"),
        "mounts": [
            {
                "source": mount.get("Source"),
                "target": mount.get("Destination"),
                "mode": mount.get("Mode"),
            }
            for mount in raw.get("Mounts") or []
        ],
        "log_driver": (host.get("LogConfig") or {}).get("Type"),
        "log_options": (host.get("LogConfig") or {}).get("Config"),
    }
    line("status", report["status"])
    line("restart", report["restart_policy"])
    line("limite de memoria", report["memory_limit"] and human_bytes(report["memory_limit"]) or "sem limite")
    line("camada gravavel", human_bytes(report["writable_layer_bytes"]))
    line("driver de log", report["log_driver"])
    line("opcoes de log", report["log_options"] or "(sem rotacao: cresce sem limite)")
    for mount in report["mounts"]:
        line("volume", f"{mount['source']} -> {mount['target']} ({mount['mode']})")

    stats = _json([runtime, "stats", "--no-stream", "--format", "{{json .}}", container])
    if isinstance(stats, dict):
        line("mem uso / limite", f"{stats.get('MemUsage', '?')} ({stats.get('MemPerc', '?')})")
        line("cpu", stats.get("CPUPerc"))
        line("processos (pids)", stats.get("PIDs"))

    if report["writable_layer_bytes"] is None:
        # ``inspect`` may omit SizeRw while the container runs; ``ps -s`` has it.
        sized = _json([runtime, "ps", "-s", "--filter", f"name={container}", "--format", "{{json .}}"])
        if isinstance(sized, list) and sized:
            report["writable_layer_bytes"] = _parse_size(sized[0].get("Size"))
            line("camada gravavel", human_bytes(report["writable_layer_bytes"]))

    for name in CGROUP_FILES:
        code, value = _run([runtime, "exec", container, "cat", f"/sys/fs/cgroup/{name}"])
        if code != 0 or not value.isdigit():
            line(f"cgroup {name}", "n/d")
        elif name == "pids.current":
            line("processos (cgroup)", f"{value} pids")
        else:
            line("memoria (cgroup)" if name == "memory.current" else f"cgroup {name}", human_bytes(int(value)))
    code, output = _run([runtime, "exec", container, "cat", "/sys/fs/cgroup/memory.stat"])
    if code == 0:
        for row in output.splitlines():
            parts = row.split()
            if len(parts) == 2 and parts[0] in CGROUP_STATS and parts[1].isdigit():
                line(f"  memory.stat {parts[0]}", human_bytes(int(parts[1])))
    return report


def host_report(runtime: str) -> dict:
    section("HOST / RUNTIME")
    code, output = _run([runtime, "system", "df", "--format", "{{json .}}"])
    if code == 0:
        print("  " + output.replace("\n", "\n  "))
    return {}


def compose_defaults() -> tuple[str | None, str | None]:
    """Imagem e container do docker-compose.yml, quando existir."""
    path = Path("docker-compose.yml")
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8")
    image = re.search(r"^\s*image:\s*(\S+)", text, re.MULTILINE)
    container = re.search(r"^\s*container_name:\s*(\S+)", text, re.MULTILINE)
    return (image.group(1) if image else None, container.group(1) if container else None)


def _parse_size(value: object) -> int:
    """Sizes arrive as '0B', '12.3MB' or '4.1kB (virtual 361MB)'."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip().upper()
    match = re.match(r"([\d.]+)\s*([KMGT]?B)", text)
    if not match:
        return 0
    factors = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    try:
        return int(float(match.group(1)) * factors[match.group(2)])
    except (ValueError, KeyError):
        return 0


def main(argv: list[str]) -> int:
    if any(item in ("-h", "--help") for item in argv):
        print(__doc__)
        return 0
    as_json = "--json" in argv
    args = [item for item in argv if not item.startswith("-")]
    default_image, default_container = compose_defaults()
    image = args[0] if args else default_image
    container = args[1] if len(args) > 1 else default_container
    if not image:
        print(__doc__)
        return 2

    runtime = detect_runtime()
    if as_json:
        # Modo maquina: nada de texto, so o resumo.
        summary = {
            "runtime": runtime,
            "image": image,
            "image_report": image_report(runtime, image),
            "container_report": container_report(runtime, container) if container else None,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"runtime: {runtime} | imagem: {image} | container: {container or '(nenhum)'}")
    image_report(runtime, image)
    contents_report(runtime, image)
    if container:
        container_report(runtime, container)
    host_report(runtime)
    print("\ndica: guarde esta saida para comparar antes/depois (ou use --json).")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
