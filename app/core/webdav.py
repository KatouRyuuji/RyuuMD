"""轻量 WebDAV 客户端（仅标准库）。

覆盖 PROPFIND / GET / PUT / MKCOL / DELETE，Basic + Digest 认证。
面向 坚果云 / Nextcloud / 群晖 / AList / Seafile 等用户自建或第三方 WebDAV。
"""

from __future__ import annotations

import base64
import email.utils
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DAV_NS = {"d": "DAV:"}
_TIMEOUT = 25


@dataclass
class DavEntry:
    relpath: str          # posix 相对路径，目录以 / 结尾
    is_dir: bool
    size: int
    mtime: float          # unix 秒；解析失败为 0


class DavError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _parse_mtime(raw: str) -> float:
    if not raw:
        return 0.0
    try:
        dt = email.utils.parsedate_to_datetime(raw.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0


def _href_path(href: str) -> str:
    """把 PROPFIND 的 href（可能是绝对 URL 或百分号编码）收成解码后的路径。"""
    raw = (href or "").strip()
    if not raw:
        return "/"
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path if parsed.scheme or parsed.netloc else raw
    path = urllib.parse.unquote(path)
    if not path.startswith("/"):
        path = "/" + path
    return path


class WebDavClient:
    """对单个 WebDAV 根目录操作。relpath 一律 posix、不以 / 开头。"""

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        insecure_ssl: bool = False,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/") + "/"
        self.username = username or ""
        self.password = password or ""
        self.insecure_ssl = bool(insecure_ssl)
        self._opener = opener or self._build_opener()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        handlers: list[urllib.request.BaseHandler] = []
        if self.username:
            pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            pm.add_password(None, self.base_url, self.username, self.password)
            handlers.append(urllib.request.HTTPBasicAuthHandler(pm))
            handlers.append(urllib.request.HTTPDigestAuthHandler(pm))
        ctx = ssl.create_default_context()
        if self.insecure_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
        return urllib.request.build_opener(*handlers)

    def abs_url(self, relpath: str = "") -> str:
        rel = (relpath or "").replace("\\", "/").lstrip("/")
        # quote 保留 /，避免中文文件名在部分服务器上 404
        return urllib.parse.urljoin(self.base_url, urllib.parse.quote(rel, safe="/"))

    def _rel_from_href(self, href: str) -> str:
        href_path = _href_path(href)
        base_path = _href_path(urllib.parse.urlparse(self.base_url).path or "/")
        if not base_path.endswith("/"):
            base_path += "/"
        if href_path.startswith(base_path):
            rel = href_path[len(base_path):]
        elif href_path == base_path.rstrip("/"):
            rel = ""
        else:
            rel = href_path.lstrip("/")
        return rel.replace("\\", "/")

    def request(
        self,
        method: str,
        relpath: str = "",
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        extra_url: str = "",
    ) -> bytes:
        url = extra_url or self.abs_url(relpath)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", "RyuuMD/1.0")
        # 预发 Basic：多数 WebDAV（坚果云/群晖）不按 RFC 先 401 再挑战
        if self.username:
            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            req.add_header("Authorization", "Basic " + token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self._opener.open(req, timeout=_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read() or b""
            except Exception:  # noqa: BLE001
                pass
            raise DavError(f"WebDAV {method} {e.code}: {e.reason}", e.code) from e
        except urllib.error.URLError as e:
            raise DavError(f"无法连接 WebDAV：{e.reason}") from e
        except OSError as e:
            raise DavError(f"无法连接 WebDAV：{e}") from e

    def probe(self) -> None:
        """探测根目录可读（Depth 0 PROPFIND）。404 时尝试 MKCOL 再建。"""
        try:
            self.propfind("", depth=0)
        except DavError as e:
            if e.status in (404, 409):
                self.mkdir("")
                self.propfind("", depth=0)
                return
            raise

    def mkdir(self, relpath: str) -> None:
        try:
            self.request("MKCOL", relpath.rstrip("/"))
        except DavError as e:
            if e.status in (405, 301, 409):  # 已存在 / 部分服务器对已有目录返回 405
                return
            raise

    def mkdir_p(self, relpath: str) -> None:
        parts = [p for p in relpath.replace("\\", "/").strip("/").split("/") if p]
        acc: list[str] = []
        for p in parts:
            acc.append(p)
            self.mkdir("/".join(acc))

    def put(self, relpath: str, data: bytes) -> None:
        parent = "/".join(relpath.replace("\\", "/").split("/")[:-1])
        if parent:
            self.mkdir_p(parent)
        self.request(
            "PUT",
            relpath,
            data=data,
            headers={"Content-Type": "application/octet-stream"},
        )

    def get(self, relpath: str) -> bytes:
        return self.request("GET", relpath)

    def delete(self, relpath: str) -> None:
        try:
            self.request("DELETE", relpath)
        except DavError as e:
            if e.status == 404:
                return
            raise

    def propfind(self, relpath: str = "", depth: int = 1) -> list[DavEntry]:
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:propfind xmlns:d="DAV:">'
            b"<d:prop><d:getlastmodified/><d:getcontentlength/><d:resourcetype/></d:prop>"
            b"</d:propfind>"
        )
        xml = self.request(
            "PROPFIND",
            relpath,
            data=body,
            headers={
                "Depth": str(depth),
                "Content-Type": "application/xml; charset=utf-8",
            },
        )
        return self._parse_propfind(xml)

    def list_files(self, prefix: str = "") -> dict[str, DavEntry]:
        """递归列出 prefix 下的文件（不含目录）。key 为相对 WebDAV 根的 posix 路径。"""
        out: dict[str, DavEntry] = {}
        stack = [prefix.strip("/")]
        seen_dirs: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen_dirs:
                continue
            seen_dirs.add(current)
            try:
                entries = self.propfind(current, depth=1)
            except DavError as e:
                if e.status == 404:
                    continue
                raise
            for ent in entries:
                rel = ent.relpath.strip("/")
                if ent.is_dir:
                    # 跳过自身
                    if rel == current.strip("/"):
                        continue
                    stack.append(rel)
                else:
                    if rel:
                        out[rel] = ent
        return out

    def _parse_propfind(self, xml: bytes) -> list[DavEntry]:
        if not xml:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            raise DavError(f"WebDAV 响应不是合法 XML：{e}") from e
        entries: list[DavEntry] = []
        # 兼容默认命名空间与 d: 前缀
        for resp in root.findall(".//{DAV:}response") or root.findall(".//d:response", DAV_NS):
            href_el = resp.find("{DAV:}href")
            if href_el is None:
                href_el = resp.find("d:href", DAV_NS)
            href = (href_el.text or "") if href_el is not None else ""
            rel = self._rel_from_href(href)
            prop = None
            for ps in resp.findall("{DAV:}propstat") or resp.findall("d:propstat", DAV_NS):
                prop = ps.find("{DAV:}prop")
                if prop is None:
                    prop = ps.find("d:prop", DAV_NS)
                if prop is not None:
                    break
            if prop is None:
                continue
            rtype = prop.find("{DAV:}resourcetype")
            if rtype is None:
                rtype = prop.find("d:resourcetype", DAV_NS)
            is_dir = False
            if rtype is not None:
                is_dir = (
                    rtype.find("{DAV:}collection") is not None
                    or rtype.find("d:collection", DAV_NS) is not None
                )
            if not is_dir and rel.endswith("/"):
                is_dir = True
            size_el = prop.find("{DAV:}getcontentlength")
            if size_el is None:
                size_el = prop.find("d:getcontentlength", DAV_NS)
            try:
                size = int((size_el.text or "0") if size_el is not None else 0)
            except ValueError:
                size = 0
            mt_el = prop.find("{DAV:}getlastmodified")
            if mt_el is None:
                mt_el = prop.find("d:getlastmodified", DAV_NS)
            mtime = _parse_mtime((mt_el.text or "") if mt_el is not None else "")
            entries.append(DavEntry(relpath=rel, is_dir=is_dir, size=size, mtime=mtime))
        return entries
