"""Small literal .env loader for CLI startup; no shell evaluation or interpolation."""
import argparse
import os
from pathlib import Path
import re
import shlex


def load_env_file(path, required=False):
    path = Path(path)
    if not path.exists() and not required:
        return
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env assignment on line {number}")
        try:
            tokens = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid .env quoting on line {number}") from None
        if len(tokens) > 1:
            raise ValueError(f"Quote .env values containing spaces on line {number}")
        values[key] = tokens[0] if tokens else ""
    # Parse completely before applying; shell environment wins over file values.
    for key, value in values.items():
        os.environ.setdefault(key, value)


def configure_cli_env(parser):
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--env-file")
    args, _ = bootstrap.parse_known_args()
    try:
        load_env_file(args.env_file or ".env", required=args.env_file is not None)
    except (ValueError, OSError) as exc:
        # Avoid including file contents or parsed secret values in errors.
        parser.error(str(exc) if isinstance(exc, ValueError) else "Cannot read the selected .env file")
    parser.add_argument("--env-file", help="Load a literal .env file (default: .env in current directory if present)")


# Profiles replace MODEL_* defaults as a unit, preventing stale endpoint/provider
# settings from another profile leaking into the selected configuration.
PROFILE_DEFAULTS = {
    "provider": "ollama", "model": None, "base_url": None, "api_key_env": None,
    "model_timeout": 180, "think": None, "max_output_tokens": None,
    "json_mode": True, "token_limit_field": None, "max_steps": 12,
    "mcp_search": False, "mcp_timeout": 10, "native_tools": False,
}


def load_profile(name, path="profiles.json"):
    """Load nonsecret settings for use by a CLI, API handler, or worker."""
    import json
    import math

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"profiles"} or not isinstance(data["profiles"], dict):
        raise ValueError("Profile file must contain a profiles object")
    if name not in data["profiles"]:
        raise ValueError("Selected profile does not exist")
    settings = data["profiles"][name]
    if not isinstance(settings, dict) or set(settings) - PROFILE_DEFAULTS.keys():
        raise ValueError("Profile contains unsupported fields; store API keys only in .env")
    if not settings.get("model") or "provider" not in settings:
        raise ValueError("Profiles require provider and model")
    effective = {**PROFILE_DEFAULTS, **settings}
    for key in ("model", "provider", "base_url", "api_key_env", "token_limit_field"):
        value = effective[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"Invalid profile field: {key}")
    if effective["provider"] not in {"ollama", "openai-compatible"}:
        raise ValueError("Invalid profile provider")
    if effective["token_limit_field"] not in {None, "max_tokens", "max_completion_tokens"}:
        raise ValueError("Invalid profile token_limit_field")
    for key in ("json_mode", "mcp_search", "native_tools"):
        if type(effective[key]) is not bool:
            raise ValueError(f"Profile field must be boolean: {key}")
    if effective["think"] is not None and type(effective["think"]) is not bool:
        raise ValueError("Profile think must be boolean or null")
    for key in ("max_steps", "max_output_tokens"):
        value = effective[key]
        if key == "max_output_tokens" and value is None:
            continue
        if type(value) is not int or value <= 0:
            raise ValueError(f"Profile field must be a positive integer: {key}")
    for key in ("model_timeout", "mcp_timeout"):
        value = effective[key]
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Profile field must be finite and positive: {key}")
    if effective["provider"] == "openai-compatible" and not effective["base_url"]:
        raise ValueError("Compatible profiles require base_url")
    return effective


def configure_cli_profiles(parser):
    import hashlib
    import json

    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--profile")
    bootstrap.add_argument("--profiles-file", default="profiles.json")
    args, _ = bootstrap.parse_known_args()
    parser.add_argument("--profile", help="Named model/tool profile (overrides MODEL_* defaults)")
    parser.add_argument("--profiles-file", default="profiles.json", help="Profile JSON file in current directory")
    parser.set_defaults(profile_hash=None)
    if args.profile is not None:
        try:
            settings = load_profile(args.profile, args.profiles_file)
        except (ValueError, OSError) as exc:
            # JSON decode errors may include file contents; never echo them.
            message = str(exc) if type(exc) is ValueError else "Cannot read or parse profile file"
            parser.error(message)
        digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
        parser.set_defaults(**settings, profile_hash=digest)
