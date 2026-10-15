"""Scoped folder access: only explicitly granted folders, ever.

The UI grants folders; per-file toggles decide what enters the model's working
context (kept client-side and sent per chat request). Reading/writing is
validated against the grant list with realpath checks.
"""
import difflib
import logging
import os
import re
import threading

from .config import get_config

log = logging.getLogger("fs")

IGNORE_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
               ".obsidian", ".idea", ".vscode", "$RECYCLE.BIN", "System Volume Information"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".json", ".csv", ".py", ".js", ".ts", ".html",
             ".css", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".log", ".xml", ".sh",
             ".bat", ".ps1", ".sql", ".rs", ".go", ".java", ".c", ".cpp", ".h"}
MAX_READ = 200_000  # chars per file read
MAX_WRITE = 5_000_000


def granted_folders():
    return list(get_config().get("folders", []))


def _resolve_inside(granted: str, target: str) -> str:
    """Return realpath of target if it is inside granted root, else raise."""
    root = os.path.realpath(granted)
    real = os.path.realpath(target)
    if real == root or real.startswith(root + os.sep) or real.startswith(root + "/"):
        return real
    raise PermissionError(f"'{target}' is outside the granted folder '{granted}'")


def find_root_for(path: str) -> str:
    for root in granted_folders():
        try:
            _resolve_inside(root, path)
            return root
        except PermissionError:
            continue
    raise PermissionError(f"Access denied: '{path}' is not inside a granted folder")


def resolve_target(path: str) -> str:
    """Realpath of the target, verified to be inside a granted folder."""
    real = os.path.realpath(_normalize(path))
    for root in granted_folders():
        try:
            return _resolve_inside(root, real)
        except PermissionError:
            continue
    raise PermissionError(f"Access denied: '{path}' is not inside a granted folder")


def _normalize(path: str) -> str:
    """Accept Windows and POSIX-style paths ('C:/x', 'C:\\x', '/c/x', '~/x')."""
    p = path.strip().replace("/", os.sep)
    if p.startswith("~"):
        p = os.path.expanduser(p)
    # Git Bash style: \c\Users\... -> C:\Users\...
    if os.name == "nt" and p.startswith(os.sep):
        m = re.match(rf"\\([a-zA-Z])\\(.*)", p)
        if m:
            p = f"{m.group(1).upper()}:\\" + m.group(2)
    return p


def grant_folder(path: str) -> dict:
    real = os.path.realpath(_normalize(path))
    if not os.path.isdir(real):
        raise NotADirectoryError(f"Folder not found: {path}")
    folders = granted_folders()
    if real not in folders:
        folders.append(real)
        get_config().set("folders", folders)
        get_config().save()
    log.info("folder granted: %s", real)
    return {"path": real}


def revoke_folder(path: str):
    folders = [f for f in granted_folders() if os.path.realpath(f) != os.path.realpath(path)]
    get_config().set("folders", folders)
    get_config().save()
    log.info("folder revoked: %s", path)


def list_dir(path: str):
    """Lazy listing of one directory (children only, UI expands level by level)."""
    real = resolve_target(path)
    if not os.path.isdir(real):
        raise NotADirectoryError(path)
    entries = []
    try:
        with os.scandir(real) as it:
            for e in it:
                if e.name.startswith(".") or e.name in IGNORE_DIRS:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        entries.append({"name": e.name, "path": e.path, "type": "dir"})
                    elif e.is_file():
                        ext = os.path.splitext(e.name)[1].lower()
                        entries.append({"name": e.name, "path": e.path, "type": "file",
                                        "size": e.stat().st_size,
                                        "text": ext in TEXT_EXTS or ext == ""})
                except OSError:
                    continue
    except OSError as e:
        raise PermissionError(str(e))
    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return entries[:500]


def read_file(path: str) -> dict:
    real = resolve_target(path)
    size = os.path.getsize(real)
    if size > MAX_READ * 10:
        raise ValueError("file too large")
    with open(real, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(MAX_READ)
    return {"path": path, "content": content, "truncated": size > MAX_READ, "size": size}


def apply_file(path: str, content: str) -> dict:
    real = resolve_target(path)
    if len(content) > MAX_WRITE:
        raise ValueError("content too large")
    backup = None
    if os.path.exists(real):
        backup = real + ".lbk.bak"
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
        with open(backup, "w", encoding="utf-8") as f:
            f.write(old)
    else:
        os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
        old = ""
    with open(real, "w", encoding="utf-8") as f:
        f.write(content)
    log.info("applied edit to %s", real)
    return {"path": real, "backup": backup}


def diff_file(path: str, new_content: str):
    real = resolve_target(path)
    old = ""
    if os.path.exists(real):
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            old = f.read(MAX_READ)
    diff = list(difflib.unified_diff(old.splitlines(), new_content.splitlines(),
                                     fromfile=path + " (current)", tofile=path + " (proposed)",
                                     lineterm=""))
    return diff[:2000]


def pick_folder_dialog() -> dict:
    """Native folder picker (tkinter) in a worker thread."""
    result = {}

    def _run():
        try:
            import tkinter as tk
            from tkinter import filedialog
            win = tk.Tk()
            win.withdraw()
            win.attributes("-topmost", True)
            path = filedialog.askdirectory(title="Grant LordBlack Harness access to folder")
            win.destroy()
            result["path"] = path or None
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=120)
    return result
