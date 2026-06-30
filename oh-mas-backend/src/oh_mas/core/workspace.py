from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorkspaceInfo:
    instance_id: str
    source_repo: Path
    workspace_root: Path
    repo_path: Path


def prepare_isolated_workspace(
    *,
    instance_id: str,
    project: str,
    repositories_root: Path,
    runtime_root: Path,
    commit_hash: str = "",
    reset_if_exists: bool = True,
) -> WorkspaceInfo:
    source_repo = (repositories_root / project).resolve()
    if not source_repo.exists():
        raise FileNotFoundError(f"Project repo not found: {source_repo}")

    workspace_root = (runtime_root / instance_id).resolve()
    repo_path = workspace_root / "repo"

    if reset_if_exists and workspace_root.exists():
        def handle_remove_readonly(func, path, exc):
            """Handle permission errors by making file writable."""
            import stat
            if isinstance(exc, PermissionError):
                Path(path).chmod(stat.S_IWRITE)
                func(path)
            else:
                raise
        shutil.rmtree(workspace_root, onexc=handle_remove_readonly)

    workspace_root.mkdir(parents=True, exist_ok=True)

    # 使用git clone确保从clean state创建workspace，避免复制工作目录的未提交修改
    # 先检查source_repo是否是git仓库
    if not (source_repo / ".git").exists():
        raise RuntimeError(f"Source repo is not a git repository: {source_repo}")

    # 使用git clone --local创建workspace（速度快且保证clean）
    subprocess.run(
        ["git", "clone", "--quiet", str(source_repo), str(repo_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # 如果指定了commit_hash，checkout到该commit
    # 跳过 "latest" 标记，表示使用当前HEAD
    if commit_hash and commit_hash != "latest":
        subprocess.run(
            ["git", "-C", str(repo_path), "checkout", "--quiet", commit_hash],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    # 清理任何可能存在的未跟踪文件
    subprocess.run(
        ["git", "-C", str(repo_path), "clean", "-fd"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    return WorkspaceInfo(
        instance_id=instance_id,
        source_repo=source_repo,
        workspace_root=workspace_root,
        repo_path=repo_path,
    )
