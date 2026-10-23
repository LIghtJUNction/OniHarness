"""Validate mod flags and isolate optional external control paths per launch."""
from pathlib import Path
import re


def validate_game_arguments(arguments):
    if not isinstance(arguments, list):
        raise ValueError("gameArgs must be a list of explicit long mod flags")
    for option in arguments:
        if (not isinstance(option, str) or
                not re.fullmatch(r"--[A-Za-z][A-Za-z0-9_-]*(?:=(?:[^\s\x00-\x1f\x7f-\x9f]| )+)?", option) or
                option.startswith("--oniharness-")):
            raise ValueError("Only explicit long mod flags may be forwarded")
        if "{" in option or "}" in option:
            value = option.partition("=")[2]
            if not value.startswith("{controlDir}/runs/{runNonce}/"):
                raise ValueError("External control templates must be under {controlDir}/runs/{runNonce}/")
            suffix = value[len("{controlDir}/runs/{runNonce}/"):]
            if (not suffix or any(part in ("", ".", "..") for part in suffix.split("/")) or
                    "{" in suffix or "}" in suffix):
                raise ValueError("Unknown or escaping external control template")
    return arguments


def resolve_game_arguments(arguments, control, run_nonce):
    validate_game_arguments(arguments)
    if not re.fullmatch(r"[0-9a-f]{32}", run_nonce):
        raise ValueError("Invalid launch namespace nonce")
    control = Path(control).resolve(strict=True)
    result = []
    for option in arguments:
        if "{controlDir}" not in option:
            result.append(option)
            continue
        key, _, value = option.partition("=")
        path = Path(value.replace("{controlDir}", str(control)).replace("{runNonce}", run_nonce))
        if not path.resolve().is_relative_to(control / "runs" / run_nonce):
            raise ValueError("External control directory escapes the current launch namespace")
        path.mkdir(parents=True, exist_ok=False)
        result.append(key + "=" + str(path))
    return result
