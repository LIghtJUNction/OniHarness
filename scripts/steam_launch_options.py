#!/usr/bin/env python3
"""Offline, byte-preserving edit of ONI's Steam LaunchOptions only.

This helper never exits Steam or starts games. Mutations require every local
Steam/game process to be gone, an expected file hash, and a private small journal.
Account state, login credentials and all unrelated config bytes are untouched.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

APP_ID = "457140"
TARGET = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps", APP_ID)
STEAM_PROCESSES = {"steam", "steamwebhelper", "steamservice", "steamerrorreporter",
                   "steamerrorreporter64", "OxygenNotIncluded", "OxygenNotIncluded.x86_64"}


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def running_clients():
    """Only process identity metadata; never return arguments/account details."""
    result = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            name = (directory / "exe").resolve(strict=True).name
            if name in STEAM_PROCESSES:
                result.append({"pid": int(directory.name), "executable": name})
        except (OSError, PermissionError):
            continue
    return result


def require_offline():
    clients = running_clients()
    if clients:
        raise RuntimeError("Steam and ONI must be fully exited before offline editing; active process count=" + str(len(clients)))


@dataclass
class Token:
    value: str
    start: int
    end: int
    kind: str


@dataclass
class Entry:
    key: Token
    value: object


@dataclass
class Object:
    entries: list
    opening: Token | None
    closing: Token | None


def tokenize(source):
    position = 0
    tokens = []
    while position < len(source):
        if source[position].isspace() or (position == 0 and source[position] == "\ufeff"):
            position += 1
            continue
        if source.startswith("//", position):
            end = source.find("\n", position)
            position = len(source) if end < 0 else end
            continue
        if source[position] in "{}":
            tokens.append(Token(source[position], position, position + 1, source[position]))
            position += 1
            continue
        start = position
        if source[position] == '"':
            position += 1
            decoded = []
            while position < len(source):
                character = source[position]
                position += 1
                if character == '"':
                    tokens.append(Token("".join(decoded), start, position, "string"))
                    break
                if character == "\\":
                    if position >= len(source):
                        raise ValueError("Unterminated VDF escape")
                    escaped = source[position]
                    position += 1
                    decoded.append({'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}.get(escaped, "\\" + escaped))
                else:
                    decoded.append(character)
            else:
                raise ValueError("Unterminated VDF string")
        else:
            while position < len(source) and not source[position].isspace() and source[position] not in "{}":
                position += 1
            if position == start:
                raise ValueError("Unexpected VDF token")
            tokens.append(Token(source[start:position], start, position, "string"))
    return tokens


def parse(source):
    tokens = tokenize(source)
    position = 0

    def block(opening=None):
        nonlocal position
        entries = []
        while position < len(tokens):
            key = tokens[position]
            position += 1
            if key.kind == "}":
                if opening is None:
                    raise ValueError("Unexpected VDF closing brace")
                return Object(entries, opening, key)
            if key.kind != "string" or position >= len(tokens):
                raise ValueError("VDF keys require a value")
            value = tokens[position]
            position += 1
            if value.kind == "{":
                value = block(value)
            elif value.kind != "string":
                raise ValueError("Malformed VDF value")
            entries.append(Entry(key, value))
        if opening is not None:
            raise ValueError("Unterminated VDF object")
        return Object(entries, None, None)

    return block()


def unique_object(document, path):
    current = document
    for key in path:
        matches = [entry.value for entry in current.entries if entry.key.value.casefold() == key.casefold()]
        if len(matches) != 1 or not isinstance(matches[0], Object):
            raise ValueError("Missing or ambiguous target VDF object; no mutation")
        current = matches[0]
    return current


def option_entry(document):
    app = unique_object(document, TARGET)
    options = [entry for entry in app.entries if entry.key.value.casefold() == "launchoptions"]
    if len(options) > 1 or (options and not isinstance(options[0].value, Token)):
        raise ValueError("Ambiguous or non-string LaunchOptions; no mutation")
    return app, options[0] if options else None


def quote(value):
    if not isinstance(value, str) or len(value) > 16384 or "\0" in value:
        raise ValueError("Launch option must be a bounded string without NUL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t") + '"'


def edit_bytes(original, value):
    source = original.decode("utf-8", errors="surrogateescape")
    document = parse(source)
    app, entry = option_entry(document)
    if entry:
        before = entry.value.value
        candidate = source[:entry.value.start] + quote(value) + source[entry.value.end:]
        previous_raw = source[entry.value.start:entry.value.end]
        was_present = True
        inserted_start, inserted_text = None, None
    else:
        before, previous_raw, was_present = None, None, False
        newline = "\r\n" if "\r\n" in source else "\n"
        closing = app.closing.start
        line_start = source.rfind("\n", 0, closing) + 1
        indentation = source[line_start:closing]
        if indentation.strip():
            insertion = newline + "\t" + '"LaunchOptions"\t\t' + quote(value) + newline
            candidate = source[:closing] + insertion + source[closing:]
            inserted_start, inserted_text = closing, insertion
        else:
            insertion = indentation + "\t" + '"LaunchOptions"\t\t' + quote(value) + newline
            candidate = source[:line_start] + insertion + source[line_start:]
            inserted_start, inserted_text = line_start, insertion
    # A second complete parse validates the edit and target identity.
    _, final = option_entry(parse(candidate))
    if final is None or final.value.value != value:
        raise RuntimeError("Launch option edit did not round-trip")
    return candidate.encode("utf-8", errors="surrogateescape"), {
        "previousValue": before, "previousRawToken": previous_raw, "wasPresent": was_present,
        "insertedStart": inserted_start, "insertedText": inserted_text,
        "installedValue": value, "beforeSha256": sha256(original), "afterSha256": sha256(candidate.encode("utf-8", errors="surrogateescape"))}


def restore_bytes(current, journal):
    source = current.decode("utf-8", errors="surrogateescape")
    _, entry = option_entry(parse(source))
    if entry is None or entry.value.value != journal["installedValue"]:
        raise RuntimeError("LaunchOptions changed since installation; refusing to overwrite user work")
    if journal["wasPresent"]:
        restored = source[:entry.value.start] + journal["previousRawToken"] + source[entry.value.end:]
    else:
        if sha256(current) == journal["afterSha256"]:
            start = journal["insertedStart"]
            inserted = journal["insertedText"]
            if source[start:start + len(inserted)] != inserted:
                raise RuntimeError("Restore insertion span no longer matches")
            restored = source[:start] + source[start + len(inserted):]
            parse(restored)
            return restored.encode("utf-8", errors="surrogateescape")
        # Remove only this key/value token; retain all surrounding bytes, even if
        # Steam has since reformatted other unrelated settings.
        restored = source[:entry.key.start] + source[entry.value.end:]
    parse(restored)
    return restored.encode("utf-8", errors="surrogateescape")


def read_config(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.name != "localconfig.vdf":
        raise ValueError("Select a real Steam userdata/config/localconfig.vdf")
    details = path.stat()
    if details.st_uid != os.getuid() or not stat.S_ISREG(details.st_mode):
        raise ValueError("Steam configuration must belong to the current user")
    if details.st_size > 32 * 1024 * 1024:
        raise ValueError("Unexpectedly large Steam configuration")
    return path.resolve(), path.read_bytes(), details


def atomic_replace(path, original, candidate, details):
    require_offline()
    if path.read_bytes() != original:
        raise RuntimeError("Steam configuration changed during editing; no mutation")
    descriptor, temporary = tempfile.mkstemp(prefix=".oniharness-launch-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(candidate)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), stat.S_IMODE(details.st_mode))
        require_offline()
        if path.read_bytes() != original:
            raise RuntimeError("Steam configuration changed before commit; no mutation")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if path.read_bytes() != candidate:
        raise RuntimeError("Steam launch-option readback mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "apply"):
        p = commands.add_parser(action)
        p.add_argument("--value", required=True)
        if action == "apply":
            p.add_argument("--expected-sha256", required=True)
            p.add_argument("--journal", required=True, type=Path)
    p = commands.add_parser("restore")
    p.add_argument("--journal", required=True, type=Path)
    p.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    path, original, details = read_config(args.config)
    if args.action == "restore":
        require_offline()
        journal = json.loads(args.journal.read_text())
        if journal["configPath"] != str(path) or journal.get("schema") != 1:
            raise ValueError("Restore journal belongs to a different configuration")
        candidate = restore_bytes(original, journal)
    else:
        candidate, journal = edit_bytes(original, args.value)
    if args.action == "plan":
        print(json.dumps({"appID": APP_ID, "beforeSha256": sha256(original), "afterSha256": sha256(candidate),
                          "targetAlreadyPresent": journal["wasPresent"], "changed": candidate != original,
                          "steamFullyExited": not running_clients(), "note": "Plan only; no account or option values emitted."}))
        return
    require_offline()
    if sha256(original) != args.expected_sha256:
        raise RuntimeError("Expected configuration SHA256 does not match; re-plan after Steam exits")
    if args.action == "apply":
        if args.journal.exists():
            raise RuntimeError("Restore journal already exists; preserve it")
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        journal.update({"schema": 1, "configPath": str(path), "appID": APP_ID})
        descriptor = os.open(args.journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(journal, output)
            output.flush()
            os.fsync(output.fileno())
    atomic_replace(path, original, candidate, details)
    print(json.dumps({"appID": APP_ID, "changed": candidate != original, "afterSha256": sha256(candidate),
                      "readbackVerified": True, "accountAuthorizationUntouched": True}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print("FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
