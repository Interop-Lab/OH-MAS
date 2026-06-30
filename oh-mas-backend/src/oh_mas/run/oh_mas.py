from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

import yaml

from oh_mas.agents.ao import AOAgent, AOConfig
from oh_mas.agents.cp import CPAgent, CPConfig
from oh_mas.agents.da import DAAgent, DAAuditConfig, NoRepoDAAgent
from oh_mas.agents.gw import GWAgent
from oh_mas.agents.gw_worker import GWWorkerConfig
from oh_mas.core.instance_loader import AlarmInstance, load_instance
from oh_mas.core.orchestrator import OHMASOrchestrator
from oh_mas.core.schemas import Alarm
from oh_mas.core.workspace import prepare_isolated_workspace
from oh_mas.oh_kb.factory import build_oh_kb_client

BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _resolve_path(path_str: str, *, base_dir: Path) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _load_local_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _load_env_candidates(config_path: Path) -> None:
    candidates = [
        config_path.parent.parent / ".env",
        config_path.parent.parent.parent / ".env",
    ]
    for env_file in candidates:
        _load_local_env(env_file)


def _extract_json_from_output(output: str) -> dict:
    for idx in range(len(output) - 1, -1, -1):
        if output[idx] != "{":
            continue
        candidate = output[idx:].strip()
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError(f"Could not parse JSON from docker output:\n{output}")


def _proxy_needs_host_network(proxy_value: str) -> bool:
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost):", proxy_value.strip(), flags=re.IGNORECASE))


def _default_cmd_template(binary: str, args: str) -> str:
    return f"{{{binary}}} {args}"


def _bind_repo_paths(cfg: dict, repo_path: str) -> None:
    cfg.setdefault("da", {})["repo_path"] = repo_path
    cfg.setdefault("cp", {})["repo_root"] = repo_path


def run(
    task_id: str,
    alarm_file: str,
    alarm_rule: str,
    alarm_message: str,
    config: Path,
    alarm_project: str = "",
    alarm_commit_hash: str = "",
    alarm_line_start: int = 1,
    alarm_line_end: int = 1,
) -> dict:
    config_path = config.resolve()
    cfg = yaml.safe_load(config_path.read_text())
    cfg_dir = config_path.parent
    runtime_cfg = cfg.get("runtime", {})
    _load_env_candidates(config_path)

    kb_cfg = cfg["oh_kb"]
    cp_cfg = cfg["cp"]
    da_cfg = cfg["da"]
    gw_cfg = cfg.get("gw", {})

    kb_client = build_oh_kb_client(
        provider=kb_cfg["provider"],
        seed_file=_resolve_path(kb_cfg["seed_file"], base_dir=cfg_dir),
        fail_open=bool(kb_cfg.get("fail_open", True)),
        graph_root=_resolve_path(kb_cfg["graph_root"], base_dir=cfg_dir) if kb_cfg.get("graph_root") else None,
        linter_examples_root=_resolve_path(kb_cfg["linter_examples_root"], base_dir=cfg_dir)
        if kb_cfg.get("linter_examples_root")
        else None,
        repair_experiences_path=_resolve_path(kb_cfg["repair_experiences_path"], base_dir=cfg_dir)
        if kb_cfg.get("repair_experiences_path")
        else None,
    )

    trace_root_cfg = runtime_cfg.get("trace_root")
    trace_root = _resolve_path(trace_root_cfg, base_dir=cfg_dir) if trace_root_cfg else None
    worker_trace_root_cfg = cp_cfg.get("worker_trace_root") or (
        str(trace_root / "cp_workers") if trace_root is not None else ""
    )

    ao_cfg = cfg.get("ao", {})
    ao = AOAgent(
        kb_client=kb_client,
        config=AOConfig(
            mode_models=cp_cfg["mode_strategy"],
            models_registry=list(cp_cfg.get("models_registry", [])),
            enable_llm_decision=bool(ao_cfg.get("enable_llm_decision", False)),
            llm_model=ao_cfg.get("llm_model", ""),
            llm_model_class=ao_cfg.get("llm_model_class", "litellm"),
            llm_temperature=float(ao_cfg.get("llm_temperature", 0.0)),
            llm_max_tokens=int(ao_cfg.get("llm_max_tokens", 800)),
            llm_timeout=int(ao_cfg.get("llm_timeout", 30)),
            kb_max_items=int(kb_cfg.get("max_items", 8)),
            kb_timeout_ms=int(kb_cfg.get("timeout_ms", 300)),
            fail_open=bool(kb_cfg.get("fail_open", True)),
        ),
    )
    gw_repo_root = cp_cfg.get("repo_root") or da_cfg.get("repo_path", "")
    gw = GWAgent(
        kb_client=kb_client,
        repo_root=str(_resolve_path(gw_repo_root, base_dir=cfg_dir)) if gw_repo_root else "",
        worker_config=GWWorkerConfig(
            enable_llm=bool(gw_cfg.get("enable_llm", True)),  # 默认启用 LLM
            llm_model=gw_cfg.get("llm_model", ""),
            llm_model_class=gw_cfg.get("llm_model_class", "openrouter"),
            llm_temperature=float(gw_cfg.get("llm_temperature", 0.0)),
            llm_max_tokens=int(gw_cfg.get("llm_max_tokens", 1200)),
            llm_timeout=int(gw_cfg.get("llm_timeout", 30)),
            max_steps=int(gw_cfg.get("max_steps", 15)),
            deterministic_fallback=bool(gw_cfg.get("deterministic_fallback", True)),
            llm_max_retries=int(gw_cfg.get("llm_max_retries", 3)),
            llm_retry_delay=float(gw_cfg.get("llm_retry_delay", 1.0)),
        ),
    )
    cp = CPAgent(
        config=CPConfig(
            provider=cp_cfg.get("provider", "litellm"),
            backend=cp_cfg.get("backend", cp_cfg.get("provider", "litellm")),
            worker_model_class=cp_cfg.get("worker_model_class", cp_cfg.get("backend", "litellm")),
            temperature=float(cp_cfg.get("temperature", 0.0)),
            max_tokens=int(cp_cfg.get("max_tokens", 1200)),
            timeout=int(cp_cfg.get("timeout", 60)),
            fallback_diff=bool(cp_cfg.get("fallback_diff", True)),
            step_limit=int(cp_cfg.get("step_limit", 10)),
            step_limits_by_mode={
                k: int(v)
                for k, v in (cp_cfg.get("step_limits_by_mode") or {}).items()
                if isinstance(v, int) or (isinstance(v, str) and v.isdigit())
            } or None,
            cost_limit=float(cp_cfg.get("cost_limit", 3.0)),
            worker_trace_root=str(_resolve_path(worker_trace_root_cfg, base_dir=cfg_dir)) if worker_trace_root_cfg else "",
            deterministic_test_mode=bool(cp_cfg.get("deterministic_test_mode", False)),
            max_parallel_workers=int(cp_cfg.get("max_parallel_workers", 3)),
            repo_root=cp_cfg.get("repo_root") or str(_resolve_path(da_cfg["repo_path"], base_dir=cfg_dir)),
        )
    )

    da_mode = da_cfg.get("mode", "strict")
    if da_mode == "no_repo":
        da = NoRepoDAAgent()
    else:
        repo_path = _resolve_path(da_cfg["repo_path"], base_dir=cfg_dir)
        da = DAAgent(
            config=DAAuditConfig(
                repo_path=repo_path,
                codelinter_target_cmd=da_cfg["codelinter_target_cmd"],
                cppcheck_target_cmd=da_cfg["cppcheck_target_cmd"],
                codelinter_repo_cmd=da_cfg["codelinter_repo_cmd"],
                cppcheck_repo_cmd=da_cfg["cppcheck_repo_cmd"],
                execution_mode=da_cfg.get("execution_mode", "host"),
                docker_image=da_cfg.get("docker_image", "harmonyrepair:latest"),
                docker_workdir=da_cfg.get("docker_workdir", "/workspace"),
                docker_extra_args=da_cfg.get("docker_extra_args", ""),
                git_bin=da_cfg.get("git_bin", "git"),
                codelinter_bin=da_cfg.get("codelinter_bin", "codelinter"),
                cppcheck_bin=da_cfg.get("cppcheck_bin", "cppcheck"),
                require_tools_preflight=bool(da_cfg.get("require_tools_preflight", True)),
            )
        )

    max_retries = int(runtime_cfg.get("max_retries", 2))
    orchestrator = OHMASOrchestrator(ao=ao, gw=gw, cp=cp, da=da, trace_root=trace_root, max_retries=max_retries)
    alarm = Alarm(
        id=f"alarm-{task_id}",
        file=alarm_file,
        rule=alarm_rule,
        line_start=max(1, int(alarm_line_start)),
        line_end=max(1, int(alarm_line_end)),
        message=alarm_message,
        project=alarm_project,
        commit_hash=alarm_commit_hash,
    )
    result = orchestrator.run(task_id=task_id, alarm=alarm)
    return asdict(result)


def _run_instance_in_docker(
    *,
    instance: AlarmInstance,
    workspace_repo: Path,
    cfg: dict,
    cfg_dir: Path,
    runtime_cfg: dict,
) -> dict:
    # Resolve source roots from this module location instead of config location.
    # This keeps docker mounts correct even when using patched configs under experiment_logs/.
    project_root = BACKEND_ROOT
    docker_image = runtime_cfg.get("docker_image", "harmonyrepair:latest")
    docker_repo_path = runtime_cfg.get("docker_repo_path", "/workspace/repo")
    docker_code_path = runtime_cfg.get("docker_code_path", "/workspace/oh_mas_src")
    docker_mini_code_path = runtime_cfg.get("docker_mini_code_path", "/workspace/mini_swe_agent_src")
    docker_mini_src_path = str(Path(docker_mini_code_path) / "src")
    mini_swe_root = (project_root.parent / "mini-swe-agent").resolve()
    if not mini_swe_root.exists():
        raise FileNotFoundError(f"mini-swe-agent source not found: {mini_swe_root}")

    container_cfg = json.loads(json.dumps(cfg))
    _bind_repo_paths(container_cfg, docker_repo_path)
    container_cfg["da"]["execution_mode"] = "host"

    # Update runtime paths to point to mounted directories in container
    docker_runtime_path = "/workspace/runtime"
    container_cfg["runtime"]["runtime_root"] = docker_runtime_path
    # Use dedicated trace mount point if trace_root is customized
    docker_trace_path = "/workspace/traces"
    container_cfg["runtime"]["trace_root"] = docker_trace_path
    if "cp" in container_cfg:
        container_cfg["cp"]["worker_trace_root"] = f"{docker_trace_path}/cp_workers"
    # Rewrite OH-KB paths for container runtime so graph/knowledge files resolve correctly.
    kb_cfg = container_cfg.get("oh_kb", {})
    kb_cfg["graph_root"] = f"{docker_runtime_path}/graph_explore"
    kb_cfg["linter_examples_root"] = "/workspace/linter_examples"
    kb_cfg["repair_experiences_path"] = "/workspace/oh_mas_data/repair_experiences.json"
    container_cfg["oh_kb"] = kb_cfg

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as cfg_tmp:
        cfg_tmp.write(yaml.safe_dump(container_cfg, sort_keys=False))
        cfg_path = Path(cfg_tmp.name)

    cmd = (
        f"python3 -m pip install -q --no-deps {shlex.quote(docker_mini_code_path)} {shlex.quote(docker_code_path)} && "
        f"python3 -m oh_mas.run.oh_mas run "
        f"--task-id {shlex.quote(instance.instance_id)} "
        f"--alarm-file {shlex.quote(instance.target_file)} "
        f"--alarm-rule {shlex.quote(instance.rule_id)} "
        f"--alarm-message {shlex.quote(instance.warning_message)} "
        f"--alarm-project {shlex.quote(instance.project)} "
        f"--alarm-commit-hash {shlex.quote(instance.commit_hash)} "
        f"--alarm-line-start {int(instance.start_line)} "
        f"--alarm-line-end {int(instance.end_line)} "
        f"--config /workspace/oh_mas_config.yaml"
    )

    env_args = ""
    needs_host_network = False
    if os.getenv("OPENROUTER_API_KEY"):
        env_args += " -e OPENROUTER_API_KEY"
    for proxy_var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        proxy_val = os.getenv(proxy_var)
        if proxy_val:
            env_args += f" -e {proxy_var}"
            if _proxy_needs_host_network(proxy_val):
                needs_host_network = True

    # Mount runtime/traces directory to preserve trace files
    runtime_root = _resolve_path(runtime_cfg["runtime_root"], base_dir=cfg_dir)
    runtime_root.mkdir(parents=True, exist_ok=True)

    # Mount trace_root separately if it differs from runtime_root (for experiment isolation)
    trace_root = _resolve_path(runtime_cfg.get("trace_root", runtime_cfg["runtime_root"] + "/traces"), base_dir=cfg_dir)
    trace_root.mkdir(parents=True, exist_ok=True)
    linter_examples_root = _resolve_path(
        cfg.get("oh_kb", {}).get("linter_examples_root", "../../linter_examples"),
        base_dir=cfg_dir,
    )
    repair_experiences_path = _resolve_path(
        cfg.get("oh_kb", {}).get("repair_experiences_path", "../data/repair_experiences.json"),
        base_dir=cfg_dir,
    )
    repair_experiences_root = repair_experiences_path.parent

    docker_extra_args = runtime_cfg.get("docker_extra_args", "")
    if needs_host_network and "--network" not in docker_extra_args:
        docker_extra_args = f"{docker_extra_args} --network host".strip()
    docker_cmd = (
        "docker run --rm"
        f"{env_args}"
        f" -v {shlex.quote(str(workspace_repo))}:{shlex.quote(docker_repo_path)}"
        f" -v {shlex.quote(str(project_root))}:{shlex.quote(docker_code_path)}"
        f" -v {shlex.quote(str(mini_swe_root))}:{shlex.quote(docker_mini_code_path)}"
        f" -v {shlex.quote(str(cfg_path))}:/workspace/oh_mas_config.yaml"
        f" -v {shlex.quote(str(runtime_root))}:{shlex.quote(docker_runtime_path)}"
        f" -v {shlex.quote(str(trace_root))}:{shlex.quote(docker_trace_path)}"
        f" -v {shlex.quote(str(linter_examples_root))}:/workspace/linter_examples"
        f" -v {shlex.quote(str(repair_experiences_root))}:/workspace/oh_mas_data"
        f" {docker_extra_args}"
        f" -w {shlex.quote(docker_repo_path)}"
        f" {shlex.quote(docker_image)}"
        f" /bin/bash -lc {shlex.quote(cmd)}"
    )

    proc = subprocess.run(
        docker_cmd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    cfg_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"Docker run failed (exit={proc.returncode}):\n{proc.stdout}")

    return _extract_json_from_output(proc.stdout)


def run_instance(instance_id: str, config: Path, dataset: Path) -> dict:
    config_path = config.resolve()
    cfg = yaml.safe_load(config_path.read_text())
    cfg_dir = config_path.parent
    _load_env_candidates(config_path)

    instance = load_instance(dataset.resolve(), instance_id)
    runtime_cfg = cfg["runtime"]
    keep_runtime = bool(runtime_cfg.get("keep_runtime", False))
    workspace = prepare_isolated_workspace(
        instance_id=instance.instance_id,
        project=instance.project,
        repositories_root=_resolve_path(runtime_cfg["repositories_root"], base_dir=cfg_dir),
        runtime_root=_resolve_path(runtime_cfg["runtime_root"], base_dir=cfg_dir),
        commit_hash=instance.commit_hash,
        reset_if_exists=True,
    )

    exec_mode = runtime_cfg.get("execution_mode", "host")
    result: dict = {}
    try:
        if exec_mode == "docker_whole":
            result = _run_instance_in_docker(
                instance=instance,
                workspace_repo=workspace.repo_path,
                cfg=cfg,
                cfg_dir=cfg_dir,
                runtime_cfg=runtime_cfg,
            )
        else:
            _bind_repo_paths(cfg, str(workspace.repo_path))
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
                tmp.write(yaml.safe_dump(cfg, sort_keys=False))
                instance_config_path = Path(tmp.name)

            original_cwd = Path.cwd()
            try:
                os.chdir(workspace.repo_path)
                result = run(
                    task_id=instance.instance_id,
                    alarm_file=instance.target_file,
                    alarm_rule=instance.rule_id,
                    alarm_message=instance.warning_message,
                    alarm_project=instance.project,
                    alarm_commit_hash=instance.commit_hash,
                    alarm_line_start=instance.start_line,
                    alarm_line_end=instance.end_line,
                    config=instance_config_path,
                )
            finally:
                instance_config_path.unlink(missing_ok=True)
                os.chdir(original_cwd)

        result["workspace"] = {
            "instance_id": instance.instance_id,
            "project": instance.project,
            "repo_path": str(workspace.repo_path),
            "execution_mode": exec_mode,
            "kept": keep_runtime,
        }
        # Standardized evaluation-facing fields.
        result["instance_id"] = instance.instance_id
        result.setdefault("model_patch", {})
        if not keep_runtime:
            result["workspace"]["repo_path"] = ""
        return result
    finally:
        if not keep_runtime:
            shutil.rmtree(workspace.workspace_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OH-MAS minimal loop")
    parser.add_argument("cmd", choices=["run", "run-instance"])
    parser.add_argument("--task-id")
    parser.add_argument("--alarm-file")
    parser.add_argument("--alarm-rule")
    parser.add_argument("--alarm-message")
    parser.add_argument("--alarm-project", default="")
    parser.add_argument("--alarm-commit-hash", default="")
    parser.add_argument("--alarm-line-start", type=int, default=1)
    parser.add_argument("--alarm-line-end", type=int, default=1)
    parser.add_argument("--instance-id")
    parser.add_argument("--dataset", default="../data/arkts_dataset_final.json")
    parser.add_argument("--config", default="config/oh_mas.yaml")
    args = parser.parse_args()

    if args.cmd == "run":
        required = [args.task_id, args.alarm_file, args.alarm_rule, args.alarm_message]
        if not all(required):
            raise SystemExit("run requires --task-id --alarm-file --alarm-rule --alarm-message")
        output = run(
            task_id=args.task_id,
            alarm_file=args.alarm_file,
            alarm_rule=args.alarm_rule,
            alarm_message=args.alarm_message,
            alarm_project=args.alarm_project,
            alarm_commit_hash=args.alarm_commit_hash,
            alarm_line_start=args.alarm_line_start,
            alarm_line_end=args.alarm_line_end,
            config=Path(args.config),
        )
    else:
        if not args.instance_id:
            raise SystemExit("run-instance requires --instance-id")
        output = run_instance(
            instance_id=args.instance_id,
            config=Path(args.config),
            dataset=Path(args.dataset),
        )

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
