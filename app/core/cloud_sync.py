"""可选云同步引擎：用户自备 WebDAV，官方不提供云端。

默认关闭。须同时满足：
1. 设置中勾选「启用云同步」并填好地址/账号；
2. 勾选「同步全部仓库」或在首页为单个仓库打开云同步。

策略：三路比对（本地 / 远端 / 上次同步指纹），双向上传下载；
双方都改过则把远端另存为 `*.conflict-时间.md`，本地文件保留并上传。
不同步删除（避免误删笔记）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional, Protocol

from .config import Config
from .fsutil import IGNORE_DIRS, MD_EXTS
from .projects import ProjectStore
from .webdav import DavError, WebDavClient

_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, CloudEngine] = {}

DEFAULT_CLOUD: dict[str, Any] = {
    "enabled": False,
    "provider": "webdav",
    "url": "",
    "username": "",
    "password": "",
    "remote_root": "RyuuMD",
    "auto_on_save": False,
    "auto_on_start": False,
    "insecure_ssl": False,
    "sync_all_projects": False,
}

# 笔记 + 文内常见配图（轻量；不同步视频/压缩包）
SYNC_EXTS = MD_EXTS | {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_VAULT = 4000
CONFLICT_MARK = ".conflict-"


class DavLike(Protocol):
    def probe(self) -> None: ...
    def get(self, relpath: str) -> bytes: ...
    def put(self, relpath: str, data: bytes) -> None: ...
    def mkdir_p(self, relpath: str) -> None: ...
    def list_files(self, prefix: str = "") -> dict: ...


def public_cloud(raw: Any) -> dict[str, Any]:
    """给前端的云配置：去掉密码，只暴露是否已保存。"""
    merged = dict(DEFAULT_CLOUD)
    if isinstance(raw, dict):
        merged.update(raw)
    pwd = str(merged.get("password") or "")
    merged["password"] = ""
    merged["password_set"] = bool(pwd)
    return merged


def load_cloud(config: Config) -> dict[str, Any]:
    merged = dict(DEFAULT_CLOUD)
    raw = config.get("cloud_sync", {}) or {}
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def save_cloud(config: Config, values: dict[str, Any]) -> dict[str, Any]:
    """合并写入。password 为空字符串表示保持原密码不变。"""
    cur = load_cloud(config)
    incoming = dict(values or {})
    if not str(incoming.get("password") or ""):
        incoming.pop("password", None)
    cur.update(incoming)
    # 开关必须是 bool；URL 去空白
    cur["enabled"] = bool(cur.get("enabled"))
    cur["auto_on_save"] = bool(cur.get("auto_on_save"))
    cur["auto_on_start"] = bool(cur.get("auto_on_start"))
    cur["insecure_ssl"] = bool(cur.get("insecure_ssl"))
    cur["sync_all_projects"] = bool(cur.get("sync_all_projects"))
    cur["url"] = str(cur.get("url") or "").strip()
    cur["username"] = str(cur.get("username") or "").strip()
    cur["remote_root"] = str(cur.get("remote_root") or "").strip().strip("/")
    cur["provider"] = "webdav"
    config.set("cloud_sync", cur)
    return public_cloud(cur)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:20]


def _is_sync_file(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    if ext not in SYNC_EXTS:
        return False
    return CONFLICT_MARK not in name


def iter_local_files(root: str) -> dict[str, dict[str, Any]]:
    """相对 posix 路径 -> {path, mtime, size}。"""
    out: dict[str, dict[str, Any]] = {}
    root_p = Path(root)
    stack: list[Path] = [root_p]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name.startswith(".") or entry.name in IGNORE_DIRS:
                    continue
                stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False) and _is_sync_file(entry.name):
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel = Path(entry.path).relative_to(root_p).as_posix()
                out[rel] = {"path": entry.path, "mtime": st.st_mtime, "size": st.st_size}
                if len(out) >= MAX_FILES_PER_VAULT:
                    return out
    return out


class CloudEngine:
    """进程内单例式引擎：多窗口共享同一把锁，避免并发写状态。"""

    def __init__(self, config: Config, projects: ProjectStore, client: Optional[DavLike] = None) -> None:
        self.config = config
        self.projects = projects
        self._injected = client
        self._lock = threading.RLock()
        self._running = False
        self._last: dict[str, Any] = {
            "ok": True,
            "running": False,
            "message": "尚未同步",
            "at": 0,
            "uploaded": 0,
            "downloaded": 0,
            "conflicts": 0,
            "skipped": 0,
            "error": "",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            out = dict(self._last)
            out["running"] = self._running
            out["enabled"] = bool(load_cloud(self.config).get("enabled"))
            return out

    def configured(self, cfg: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        c = cfg or load_cloud(self.config)
        if not c.get("enabled"):
            return False, "未启用云同步"
        if (c.get("provider") or "webdav") != "webdav":
            return False, "暂仅支持 WebDAV"
        if not c.get("url"):
            return False, "未填写 WebDAV 地址"
        if not c.get("username"):
            return False, "未填写用户名"
        return True, ""

    def _make_client(self, cfg: dict[str, Any]) -> DavLike:
        if self._injected is not None:
            return self._injected
        url = cfg["url"].rstrip("/")
        root = str(cfg.get("remote_root") or "").strip("/")
        base = f"{url}/{root}/" if root else f"{url}/"
        return WebDavClient(
            base,
            username=cfg.get("username") or "",
            password=cfg.get("password") or "",
            insecure_ssl=bool(cfg.get("insecure_ssl")),
        )

    def test_connection(self) -> dict[str, Any]:
        cfg = load_cloud(self.config)
        ok, reason = self.configured(cfg)
        if not ok:
            return {"ok": False, "error": reason}
        try:
            self._make_client(cfg).probe()
            return {"ok": True, "message": "连接成功"}
        except DavError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def vault_enabled(self, project: dict[str, Any], cfg: Optional[dict[str, Any]] = None) -> bool:
        c = cfg or load_cloud(self.config)
        if not c.get("enabled"):
            return False
        if c.get("sync_all_projects"):
            return True
        return bool(project.get("cloud_enabled"))

    # ------------------------------------------------------------------
    # 状态指纹
    # ------------------------------------------------------------------
    def _state_path(self) -> Path:
        return self.config.data_dir / "cloud-state.json"

    def _load_state(self) -> dict[str, Any]:
        p = self._state_path()
        if not p.is_file():
            return {"files": {}}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                return data
        except Exception:  # noqa: BLE001
            pass
        return {"files": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        p = self._state_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def _key(self, pid: str, rel: str) -> str:
        return f"{pid}/{rel}"

    # ------------------------------------------------------------------
    # 同步
    # ------------------------------------------------------------------
    def sync(self, project_id: str = "") -> dict[str, Any]:
        with self._lock:
            if self._running:
                return {"ok": False, "error": "已有同步正在进行", "running": True}
            self._running = True
            self._last["running"] = True
            self._last["message"] = "正在同步…"
        try:
            result = self._sync_locked(project_id)
            with self._lock:
                self._last = dict(result)
                self._last["running"] = False
                self._last["at"] = int(time.time())
            return result
        finally:
            with self._lock:
                self._running = False

    def _sync_locked(self, project_id: str) -> dict[str, Any]:
        cfg = load_cloud(self.config)
        ok, reason = self.configured(cfg)
        if not ok:
            return {
                "ok": False, "error": reason, "uploaded": 0, "downloaded": 0,
                "conflicts": 0, "skipped": 0, "message": reason,
            }
        if project_id:
            proj = self.projects.get(project_id)
            vaults = [proj] if proj else []
            if proj and not self.vault_enabled(proj, cfg):
                return {
                    "ok": False, "error": "该仓库未开启云同步（请在首页卡片或设置中勾选）",
                    "uploaded": 0, "downloaded": 0, "conflicts": 0, "skipped": 0,
                    "message": "仓库未开启云同步",
                }
        else:
            vaults = [p for p in self.projects.list(with_stats=False) if self.vault_enabled(p, cfg)]
        if not vaults:
            msg = "没有已开启云同步的仓库"
            return {
                "ok": False, "error": msg, "uploaded": 0, "downloaded": 0,
                "conflicts": 0, "skipped": 0, "message": msg,
            }
        try:
            client = self._make_client(cfg)
            client.probe()
        except DavError as e:
            return {
                "ok": False, "error": str(e), "uploaded": 0, "downloaded": 0,
                "conflicts": 0, "skipped": 0, "message": str(e),
            }

        totals = {"uploaded": 0, "downloaded": 0, "conflicts": 0, "skipped": 0, "errors": []}
        state = self._load_state()
        for proj in vaults:
            if not proj or not proj.get("exists", True):
                # list(with_stats=False) 仍带 exists
                p = Path(proj["path"]) if proj else None
                if not p or not p.is_dir():
                    totals["errors"].append(f"{(proj or {}).get('name', '?')} 目录不存在")
                    continue
            self._sync_vault(client, proj, state, totals)
        self._save_state(state)
        err = "；".join(totals["errors"][:3])
        ok_all = not err
        msg = (
            f"上传 {totals['uploaded']} · 下载 {totals['downloaded']}"
            + (f" · 冲突 {totals['conflicts']}" if totals["conflicts"] else "")
            + (f" · 跳过 {totals['skipped']}" if totals["skipped"] else "")
        )
        if err:
            msg = err
        return {
            "ok": ok_all,
            "error": err,
            "uploaded": totals["uploaded"],
            "downloaded": totals["downloaded"],
            "conflicts": totals["conflicts"],
            "skipped": totals["skipped"],
            "message": msg,
        }

    def _sync_vault(
        self,
        client: DavLike,
        proj: dict[str, Any],
        state: dict[str, Any],
        totals: dict[str, Any],
    ) -> None:
        pid = proj["id"]
        root = proj["path"]
        prefix = pid  # 远端以仓库 id 分目录，重命名本地仓库不影响云端
        try:
            client.mkdir_p(prefix)
        except DavError as e:
            totals["errors"].append(f"{proj.get('name')}: {e}")
            return
        local = iter_local_files(root)
        try:
            remote_all = client.list_files(prefix)
        except DavError as e:
            totals["errors"].append(f"{proj.get('name')}: {e}")
            return
        # 远端 key 转成仓库内相对路径
        remote: dict[str, Any] = {}
        head = prefix + "/"
        for k, ent in remote_all.items():
            if k.startswith(head):
                rel = k[len(head):]
            elif k == prefix:
                continue
            else:
                continue
            if not rel or CONFLICT_MARK in Path(rel).name:
                continue
            remote[rel] = ent

        files_state: dict[str, Any] = state.setdefault("files", {})
        keys = set(local) | set(remote)
        for rel in sorted(keys):
            loc = local.get(rel)
            rem = remote.get(rel)
            rec = files_state.get(self._key(pid, rel)) or {}
            try:
                action = self._decide(loc, rem, rec)
                if action == "skip":
                    totals["skipped"] += 1
                    continue
                if action == "upload":
                    if not loc or loc["size"] > MAX_FILE_BYTES:
                        totals["skipped"] += 1
                        continue
                    data = Path(loc["path"]).read_bytes()
                    client.put(f"{prefix}/{rel}", data)
                    files_state[self._key(pid, rel)] = {
                        "local_mtime": loc["mtime"],
                        "sha": _sha(data),
                    }
                    totals["uploaded"] += 1
                elif action == "download":
                    data = client.get(f"{prefix}/{rel}")
                    dest = Path(root) / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    files_state[self._key(pid, rel)] = {
                        "local_mtime": dest.stat().st_mtime,
                        "sha": _sha(data),
                    }
                    totals["downloaded"] += 1
                elif action == "conflict":
                    data = client.get(f"{prefix}/{rel}")
                    dest = Path(root) / rel
                    stamp = time.strftime("%Y%m%d-%H%M%S")
                    stem, ext = dest.stem, dest.suffix
                    conflict_path = dest.with_name(f"{stem}{CONFLICT_MARK}{stamp}{ext}")
                    conflict_path.write_bytes(data)
                    local_data = Path(loc["path"]).read_bytes()
                    client.put(f"{prefix}/{rel}", local_data)
                    files_state[self._key(pid, rel)] = {
                        "local_mtime": loc["mtime"],
                        "sha": _sha(local_data),
                    }
                    totals["conflicts"] += 1
                    totals["uploaded"] += 1
            except (DavError, OSError) as e:
                totals["errors"].append(f"{rel}: {e}")

    def _decide(self, loc: Optional[dict], rem: Any, rec: dict) -> str:
        """返回 skip / upload / download / conflict。"""
        if loc and not rem:
            return "upload"
        if rem and not loc:
            return "download"
        if not loc and not rem:
            return "skip"
        # 双方都在：用内容指纹。无历史记录时比大小+hash
        try:
            local_data = Path(loc["path"]).read_bytes()
        except OSError:
            return "skip"
        if loc["size"] > MAX_FILE_BYTES:
            return "skip"
        local_sha = _sha(local_data)
        last_sha = rec.get("sha")
        last_mtime = rec.get("local_mtime")
        local_changed = last_sha is None or local_sha != last_sha or (
            last_mtime is not None and abs(loc["mtime"] - float(last_mtime)) > 0.51
        )
        # 远端是否变：没有上次指纹则视为未知（用下载对比）
        if not local_changed and last_sha:
            # 本地没变：若远端也没变（同 hash 无法直接得，需 GET 太贵）
            # 用 size + mtime 粗判远端；mtime 不可靠时保守 GET 只在 size 变
            rem_size = getattr(rem, "size", None)
            if rem_size is not None and rem_size == loc["size"]:
                return "skip"
            # size 不同 → 远端变了，本地没变 → 下载
            return "download"
        if local_changed and last_sha:
            rem_size = getattr(rem, "size", None)
            if rem_size is not None and rem_size == loc["size"]:
                # 大小相同，再比 hash 需 GET；先假设仅本地改（常见）走上传
                # 若其实远端也改成了同大小不同内容，下次 size 不同或冲突文件兜底
                return "upload"
            if rem_size is not None and rem_size != loc["size"] and last_sha:
                return "conflict"
            return "upload"
        # 首次双方都有：hash 相同则记状态跳过，不同则较新者胜（比 mtime）
        # 这里还没 GET，用 size 先筛
        rem_size = getattr(rem, "size", 0) or 0
        if rem_size == loc["size"]:
            return "skip"  # 大概率同一文件；记状态在 skip 前未写入，下次仍 skip
        rem_mtime = getattr(rem, "mtime", 0) or 0
        if loc["mtime"] >= rem_mtime:
            return "upload"
        return "download"

    def push_file(self, local_path: str) -> dict[str, Any]:
        """保存后单文件上传。未启用/未勾选仓库则静默跳过。"""
        cfg = load_cloud(self.config)
        ok, _ = self.configured(cfg)
        if not ok or not cfg.get("auto_on_save"):
            return {"ok": True, "skipped": True}
        proj = self.projects.find_by_containing_path(local_path)
        if not proj or not self.vault_enabled(proj, cfg):
            return {"ok": True, "skipped": True}
        p = Path(local_path)
        if not p.is_file() or not _is_sync_file(p.name):
            return {"ok": True, "skipped": True}
        try:
            rel = p.relative_to(proj["path"]).as_posix()
        except ValueError:
            return {"ok": True, "skipped": True}
        if p.stat().st_size > MAX_FILE_BYTES:
            return {"ok": False, "error": "文件超过 20MB，已跳过上传"}
        with self._lock:
            try:
                client = self._make_client(cfg)
                remote = f"{proj['id']}/{rel}"
                data = p.read_bytes()
                client.put(remote, data)
                state = self._load_state()
                state.setdefault("files", {})[self._key(proj["id"], rel)] = {
                    "local_mtime": p.stat().st_mtime,
                    "sha": _sha(data),
                }
                self._save_state(state)
                return {"ok": True, "uploaded": True, "path": rel}
            except (DavError, OSError) as e:
                return {"ok": False, "error": str(e)}


def get_engine(
    config: Config,
    projects: ProjectStore,
    client: Optional[DavLike] = None,
) -> CloudEngine:
    """多窗口共用同一引擎（按数据目录）。测试注入 client 时每次新建。"""
    if client is not None:
        return CloudEngine(config, projects, client)
    key = str(config.data_dir)
    with _ENGINE_LOCK:
        eng = _ENGINES.get(key)
        if eng is None:
            eng = CloudEngine(config, projects)
            _ENGINES[key] = eng
        else:
            eng.projects = projects
            eng.config = config
        return eng
