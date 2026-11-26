"""Changing the configuration file in place: only the keys asked for (#117).

PyYAML, the one YAML library flib depends on, reads YAML but cannot write it
back with its comments. ``flib init`` writes a commented file, and people add
their own notes, so rewriting the whole file from the parsed data would lose
them. This module edits the text instead, at the positions PyYAML's composer
reports for each node:

- ``Set`` replaces a scalar's text, or a collection's, where it stands, and adds
  a key that is missing (and the sections above it) at the end of its section;
- ``Delete`` removes a key's lines, or an item's;
- ``Append`` adds an item at the end of a list.

Everything around the edited node, comments and blank lines and the order of
keys, stays byte for byte. A collection written inline (``{name: fake, model:
x}``, ``[bill, tax]``) is written again inline as a whole, which drops a
comment inside it. Every edit is checked: the new text must parse to exactly
the data the edit asked for (the old data with that one change), else
``EditError`` and nothing is written. That catches what this approach does not
handle (anchors and aliases, tags, odd layouts): the page then says to change
the file by hand.

``write`` saves a new text atomically (a temporary file in the same folder,
mode 0600, then a rename), after a timestamped backup of the old one, also
0600; the latest ``KEEP_BACKUPS`` are kept. A file that is a symbolic link is
never replaced (that would cut the link): it is refused.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

KEEP_BACKUPS = 10
"""Backups kept next to the file; older ones are deleted when a new one is made."""
BACKUP_MARK = ".backup-"
PIN_LENGTH = 20

PathPart = str | int
KeyPath = tuple[PathPart, ...]


class EditError(Exception):
    """The edit could not be made safely; nothing was written. The message has no path."""


@dataclass(frozen=True, slots=True)
class Set:
    path: KeyPath
    value: Any


@dataclass(frozen=True, slots=True)
class Delete:
    path: KeyPath


@dataclass(frozen=True, slots=True)
class Append:
    path: KeyPath
    value: Any


Edit = Set | Delete | Append


def pin(text: bytes) -> str:
    """The file's state as a page showed it: a digest of its bytes."""
    return hashlib.sha256(text).hexdigest()[:PIN_LENGTH]


# --- the data --------------------------------------------------------------------------


def apply_data(raw: dict[str, Any], edits: list[Edit]) -> dict[str, Any]:
    """``raw`` with ``edits`` made, as a new object: what the new text must parse to."""
    data = copy.deepcopy(raw)
    try:
        for edit in edits:
            _apply_one(data, edit)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise EditError("the file is not as expected") from exc
    return data


def _apply_one(data: Any, edit: Edit) -> None:
    *parents, last = edit.path
    here = data
    for part in parents:
        if isinstance(part, int):
            here = here[part]
        else:
            if not isinstance(here.get(part), dict | list):
                if isinstance(edit, Delete):
                    return
                here[part] = {}
            here = here[part]
    if isinstance(edit, Set):
        here[last] = copy.deepcopy(edit.value)
    elif isinstance(edit, Delete):
        if isinstance(last, int):
            del here[last]
        else:
            here.pop(last, None)
    else:
        if here.get(last) is None:
            here[last] = []
        here[last].append(copy.deepcopy(edit.value))


# --- the text --------------------------------------------------------------------------


def edit_text(text: str, edits: list[Edit]) -> str:
    """``text`` with ``edits`` made in place; raises ``EditError`` when that cannot be
    done exactly (the check: the result parses to ``apply_data`` of the old data)."""
    raw = load(text)
    expected = apply_data(raw, edits)
    done: list[Edit] = []
    for edit in edits:
        done.append(edit)
        text = _Text(text).apply(edit, apply_data(raw, done))
    try:
        result = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EditError("the change would not be valid YAML") from exc
    if result != expected:
        raise EditError("the file is laid out in a way this page cannot change safely")
    return text


def load(text: str) -> dict[str, Any]:
    """The data of a configuration's text; ``EditError`` unless it is one mapping."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EditError(yaml_problem(exc)) from exc
    if not isinstance(data, dict):
        raise EditError("the configuration must be a YAML mapping")
    return data


def yaml_problem(exc: yaml.YAMLError) -> str:
    """Where the YAML is broken, without quoting the file (it could hold anything)."""
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return "the file is not valid YAML"
    return f"the file is not valid YAML (line {mark.line + 1}, column {mark.column + 1})"


_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _key(name: PathPart) -> str:
    text = str(name)
    return text if _PLAIN_KEY.fullmatch(text) else json.dumps(text, ensure_ascii=False)


def scalar(value: Any) -> str:
    """A YAML scalar for ``value``: strings double-quoted (JSON strings are valid YAML)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        if "e" in text or "E" in text or "inf" in text or "nan" in text:
            raise EditError("a number this page cannot write")
        return text
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise EditError("a value this page cannot write")


def flow(value: Any) -> str:
    """``value`` written inline: ``{a: 1, b: [x, y]}``."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_key(k)}: {flow(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(flow(v) for v in value) + "]"
    return scalar(value)


def _inline(value: Any) -> bool:
    """Written on the key's own line: a scalar, an empty collection, a list of scalars."""
    if isinstance(value, dict | list) and not value:
        return True
    if isinstance(value, list):
        return all(not isinstance(v, dict | list) for v in value)
    return not isinstance(value, dict)


def block(value: Any, indent: int) -> str:
    """``value`` (a mapping or a list) as block YAML, each line indented by ``indent``,
    ending with a newline."""
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if _inline(v):
                lines.append(f"{pad}{_key(k)}: {flow(v)}\n")
            else:
                lines.append(f"{pad}{_key(k)}:\n")
                lines.append(block(v, indent + 2))
    elif isinstance(value, list):
        for item in value:
            if _inline(item):
                lines.append(f"{pad}- {flow(item)}\n")
            else:
                inner = block(item, indent + 2)
                lines.append(pad + "- " + inner[indent + 2 :])
    else:
        raise EditError("a value this page cannot write")
    return "".join(lines)


def _last(node: Node) -> int:
    """Where ``node``'s own text ends: its last scalar (a block collection's end mark is
    where the next thing starts, past comments and blank lines)."""
    if isinstance(node, MappingNode) and node.value and not node.flow_style:
        return _last(node.value[-1][1])
    if isinstance(node, SequenceNode) and node.value and not node.flow_style:
        return _last(node.value[-1])
    return int(node.end_mark.index)


def _shared(root: Node) -> bool:
    """Whether some node appears twice in the tree (an alias)."""
    seen: set[int] = set()
    todo: list[Node] = [root]
    while todo:
        node = todo.pop()
        if id(node) in seen:
            return True
        seen.add(id(node))
        if isinstance(node, MappingNode):
            for key, value in node.value:
                todo += [key, value]
        elif isinstance(node, SequenceNode):
            todo += node.value
    return False


class _Text:
    """One edit on one text, at the positions of its composed nodes."""

    def __init__(self, text: str) -> None:
        self.text = text
        try:
            root = yaml.compose(text, Loader=yaml.SafeLoader)
        except yaml.YAMLError as exc:
            raise EditError(yaml_problem(exc)) from exc
        if not isinstance(root, MappingNode) or root.flow_style:
            raise EditError("the file is laid out in a way this page cannot change safely")
        if _shared(root):
            # An alias (``*name``) is the same node in two places: an edit at one would
            # change the other, and the data check could not tell.
            raise EditError("the file uses YAML aliases (*name), which this page leaves alone")
        self.root = root

    # --- positions ---

    def _line_start(self, index: int) -> int:
        return self.text.rfind("\n", 0, index) + 1

    def _line_end(self, index: int) -> int:
        """Just past the end of the line ``index`` is on (its newline included)."""
        if index > 0 and self.text[index - 1] == "\n":
            return index
        found = self.text.find("\n", index)
        return len(self.text) if found < 0 else found + 1

    def _splice(self, start: int, end: int, new: str) -> str:
        return self.text[:start] + new + self.text[end:]

    def _insert_lines(self, after: int, lines: str) -> str:
        """``lines`` (each ending with a newline) after the line ``after`` is on."""
        at = self._line_end(after)
        lead = "" if at == 0 or self.text[at - 1] == "\n" else "\n"
        return self._splice(at, at, lead + lines)

    # --- finding nodes ---

    @staticmethod
    def _child(node: Node, part: PathPart) -> tuple[Node | None, Node | None]:
        """(the key node, the value node) of ``part`` in ``node``; ``(None, None)``."""
        if isinstance(node, MappingNode) and isinstance(part, str):
            found = [(k, v) for k, v in node.value if isinstance(k, ScalarNode) and k.value == part]
            return found[-1] if found else (None, None)  # PyYAML keeps the last of two
        if isinstance(node, SequenceNode) and isinstance(part, int):
            if 0 <= part < len(node.value):
                return None, node.value[part]
            raise EditError("the list changed")
        return None, None

    def _walk(self, path: KeyPath) -> list[tuple[Node | None, Node]]:
        """``[(key node, value node), ...]`` along ``path`` as far as it exists."""
        chain: list[tuple[Node | None, Node]] = [(None, self.root)]
        for part in path:
            key, value = self._child(chain[-1][1], part)
            if value is None:
                break
            chain.append((key, value))
        return chain

    @staticmethod
    def _flow_at(chain: list[tuple[Node | None, Node]]) -> int | None:
        """The depth of the outermost inline collection on ``chain``, if any."""
        for depth, (_, node) in enumerate(chain):
            if isinstance(node, MappingNode | SequenceNode) and node.flow_style:
                return depth
        return None

    @staticmethod
    def _value_at(data: Any, path: KeyPath) -> Any:
        for part in path:
            data = data[part]
        return data

    # --- the edit ---

    def apply(self, edit: Edit, after: dict[str, Any]) -> str:
        chain = self._walk(edit.path)
        exists = len(chain) - 1 == len(edit.path)
        if isinstance(edit, Delete) and not exists:
            return self.text
        # Anything in an inline collection: that whole collection, written again.
        depth = self._flow_at(chain[:-1] if isinstance(edit, Delete) else chain)
        if depth is not None:
            node = chain[depth][1]
            value = self._value_at(after, edit.path[:depth])
            return self._splice(node.start_mark.index, node.end_mark.index, flow(value))
        if isinstance(edit, Set):
            return self._set(chain, edit, exists)
        if isinstance(edit, Delete):
            return self._delete(chain)
        return self._append(chain, edit, exists)

    def _set(self, chain: list[tuple[Node | None, Node]], edit: Set, exists: bool) -> str:
        if exists:
            key, node = chain[-1]
            return self._replace(key, node, edit.value)
        depth = len(chain) - 1
        key, node = chain[-1]
        rest = edit.path[depth:]
        if any(isinstance(p, int) for p in rest):
            raise EditError("the list changed")
        value: Any = edit.value
        for part in reversed(rest[1:]):
            value = {part: value}
        if isinstance(node, ScalarNode):
            if node.value not in ("", "null", "~"):
                raise EditError("the file is laid out in a way this page cannot change safely")
            return self._replace(key, node, {rest[0]: value})
        if not isinstance(node, MappingNode):
            raise EditError("the file is laid out in a way this page cannot change safely")
        indent = int(node.value[0][0].start_mark.column)
        return self._insert_lines(_last(node), block({rest[0]: value}, indent))

    def _replace(self, key: Node | None, node: Node, value: Any) -> str:
        start, end = int(node.start_mark.index), _last(node)
        block_scalar = isinstance(node, ScalarNode) and node.style in ("|", ">")
        if block_scalar and self.text[start:end].endswith("\n"):
            end -= 1  # the line break ends the key's line, as before
        if isinstance(node, SequenceNode) and isinstance(value, list) and value:
            start = self._dash(node)  # a block list stays a block list
            column = start - self._line_start(start)
            return self._splice(start, end, block(value, column)[column:].rstrip("\n"))
        if isinstance(node, MappingNode) and isinstance(value, dict) and value:
            first = node.value[0][0] if node.value else node
            start, column = int(first.start_mark.index), int(first.start_mark.column)
            return self._splice(start, end, block(value, column)[column:].rstrip("\n"))
        if _inline(value):
            return self._splice(start, end, flow(value))
        if not isinstance(node, ScalarNode) or key is None:
            raise EditError("the file is laid out in a way this page cannot change safely")
        # A section with nothing in it yet (``schedule:``): its keys under it.
        indent = int(key.start_mark.column) + 2
        return self._splice(start, end, "\n" + block(value, indent).rstrip("\n"))

    def _dash(self, seq: SequenceNode) -> int:
        """Where the ``-`` of a block list's first item is."""
        first = int(seq.value[0].start_mark.index)
        dash = self.text.rfind("-", self._line_start(first), first)
        if dash < 0 or self.text[dash + 1 : first].strip():
            raise EditError("the file is laid out in a way this page cannot change safely")
        return dash

    def _delete(self, chain: list[tuple[Node | None, Node]]) -> str:
        key, node = chain[-1]
        start = int(node.start_mark.index) if key is None else int(key.start_mark.index)
        line = self._line_start(start)
        lead = self.text[line:start]
        if key is None:  # an item of a block list: its "- " goes too
            if not re.fullmatch(r"[ \t]*-[ \t]*", lead):
                raise EditError("the file is laid out in a way this page cannot change safely")
            start = line + len(lead) - len(lead.lstrip(" \t"))
        elif lead.strip():
            raise EditError("the file is laid out in a way this page cannot change safely")
        parent = chain[-2][1]
        if len(parent.value) == 1:  # the last one: an empty collection stays
            empty = "[]" if isinstance(parent, SequenceNode) else "{}"
            begin = start if key is None else int(key.start_mark.index)
            return self._splice(begin, _last(node), empty)
        return self._splice(line, self._line_end(_last(node)), "")

    def _append(self, chain: list[tuple[Node | None, Node]], edit: Append, exists: bool) -> str:
        if not exists:
            return self._set(chain, Set(edit.path, [edit.value]), exists)
        node = chain[-1][1]
        if isinstance(node, ScalarNode):
            return self._set(chain, Set(edit.path, [edit.value]), exists)
        if not isinstance(node, SequenceNode):
            raise EditError("the file is laid out in a way this page cannot change safely")
        dash = self._dash(node)
        indent = dash - self._line_start(dash)
        return self._insert_lines(_last(node), block([edit.value], indent))


# --- the file --------------------------------------------------------------------------


def backups(path: Path) -> list[Path]:
    """The backups of ``path`` next to it, newest first."""
    prefix = path.name + BACKUP_MARK
    try:
        found = [p for p in path.parent.iterdir() if p.name.startswith(prefix)]
    except OSError:
        return []
    return sorted((p for p in found if _own_file(p)), key=lambda p: p.name, reverse=True)


def _own_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()


def _create(path: Path, data: bytes) -> None:
    """A new file, mode 0600, never through a link and never over an existing file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def check_writable(path: Path) -> None:
    """Refuse a file that is a link or not the user's own: replacing it would cut the
    link (or take over someone else's file)."""
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise EditError("the configuration file is missing") from exc
    if stat.S_ISLNK(info.st_mode):
        raise EditError("the configuration file is a symbolic link; change it by hand")
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise EditError("the configuration file is not a plain file of yours")


def write(path: Path, old: bytes, new: str, *, now: datetime | None = None) -> Path:
    """Save ``new`` as ``path`` atomically, after a backup of ``old`` (its bytes now);
    returns the backup. Raises ``OSError`` or ``EditError``; then nothing changed."""
    check_writable(path)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}{BACKUP_MARK}{stamp}")
    number = 1
    while True:
        try:
            _create(backup, old)
            break
        except FileExistsError:
            number += 1
            backup = path.with_name(f"{path.name}{BACKUP_MARK}{stamp}-{number:02d}")
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        _create(tmp, new.encode("utf-8"))
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _sync_folder(path.parent)
    for stale in backups(path)[KEEP_BACKUPS:]:
        stale.unlink(missing_ok=True)
    return backup


def _sync_folder(folder: Path) -> None:
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
