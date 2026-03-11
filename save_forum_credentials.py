#!/usr/bin/env python3
"""Persist forum tokens outside the database and keep a legacy mirror."""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_CREDENTIALS_DIR = (
    os.environ.get("FORUM_CREDENTIALS_DIR") or "/home/cori/.openclaw/credentials"
).strip() or "/home/cori/.openclaw/credentials"
LEGACY_INSTANCES_FILE = (
    os.environ.get("FORUM_LEGACY_INSTANCES_FILE") or os.path.join(BASE_DIR, "instances.json")
).strip() or os.path.join(BASE_DIR, "instances.json")


def atomic_write_text(path, content):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def credential_paths(agent_id):
    safe_id = (agent_id or "").strip()
    return (
        os.path.join(DEFAULT_CREDENTIALS_DIR, f"forum-{safe_id}.json"),
        os.path.join(DEFAULT_CREDENTIALS_DIR, f"forum-{safe_id}.token"),
    )


def extract_payload(raw):
    agent_id = (raw.get("agent_id") or "").strip()
    forum_cfg = ((raw.get("config") or {}).get("forum") or {})
    token = (raw.get("token") or forum_cfg.get("token") or "").strip()
    if not agent_id or not token:
        raise ValueError("missing agent_id or token in response payload")
    return {
        "agent_id": agent_id,
        "author_id": agent_id,
        "name": (raw.get("name") or "").strip(),
        "color": (raw.get("color") or "").strip(),
        "url": (forum_cfg.get("url") or raw.get("url") or "http://no-public-ip").strip() or "http://no-public-ip",
        "token": token,
        "api_url": (forum_cfg.get("api_url") or "").strip(),
        "guide_url": (forum_cfg.get("guide_url") or "").strip(),
        "guide_token_header": (forum_cfg.get("guide_token_header") or "").strip(),
        "instances_url": (forum_cfg.get("instances_url") or "").strip(),
        "connect_url": (forum_cfg.get("connect_url") or "").strip(),
        "react_url_template": (forum_cfg.get("react_url_template") or "").strip(),
    }


def save_credentials(entry):
    json_path, token_path = credential_paths(entry["agent_id"])
    payload = dict(entry)
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(token_path, entry["token"] + "\n")
    try:
        os.chmod(json_path, 0o600)
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    sync_legacy_instances(entry)
    return json_path, token_path


def sync_legacy_instances(entry):
    data = load_json(LEGACY_INSTANCES_FILE, {})
    if not isinstance(data, dict):
        data = {}
    previous = data.get(entry["agent_id"]) if isinstance(data.get(entry["agent_id"]), dict) else {}
    data[entry["agent_id"]] = {
        "name": entry["name"] or previous.get("name") or entry["agent_id"],
        "color": entry["color"] or previous.get("color") or "#94a3b8",
        "author_id": entry["agent_id"],
        "url": entry["url"] or previous.get("url") or "http://no-public-ip",
        "token": entry["token"],
        "created_at": previous.get("created_at") or "",
    }
    atomic_write_text(LEGACY_INSTANCES_FILE, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def print_token(agent_id):
    _, token_path = credential_paths(agent_id)
    if not os.path.exists(token_path):
        return 1
    sys.stdout.write(Path(token_path).read_text(encoding="utf-8").strip())
    return 0


def build_manual_entry(args):
    if not args.agent_id or not args.token:
        raise ValueError("--agent-id and --token are required in manual mode")
    return {
        "agent_id": args.agent_id.strip(),
        "author_id": args.agent_id.strip(),
        "name": (args.name or "").strip(),
        "color": (args.color or "").strip(),
        "url": (args.url or "http://no-public-ip").strip() or "http://no-public-ip",
        "token": args.token.strip(),
        "api_url": (args.api_url or "").strip(),
        "guide_url": (args.guide_url or "").strip(),
        "guide_token_header": (args.guide_token_header or "").strip(),
        "instances_url": (args.instances_url or "").strip(),
        "connect_url": (args.connect_url or "").strip(),
        "react_url_template": (args.react_url_template or "").strip(),
    }


def main():
    parser = argparse.ArgumentParser(description="Persist forum register/connect credentials.")
    parser.add_argument("--print-token", action="store_true", help="Print the saved token for an agent_id.")
    parser.add_argument("--agent-id")
    parser.add_argument("--token")
    parser.add_argument("--name")
    parser.add_argument("--color")
    parser.add_argument("--url")
    parser.add_argument("--api-url")
    parser.add_argument("--guide-url")
    parser.add_argument("--guide-token-header")
    parser.add_argument("--instances-url")
    parser.add_argument("--connect-url")
    parser.add_argument("--react-url-template")
    args = parser.parse_args()

    if args.print_token:
        return print_token(args.agent_id)

    try:
        if args.agent_id and args.token:
            entry = build_manual_entry(args)
        else:
            raw = sys.stdin.read().strip()
            if not raw:
                raise ValueError("stdin is empty; expected register/connect response JSON")
            entry = extract_payload(json.loads(raw))
        json_path, token_path = save_credentials(entry)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({
        "status": "ok",
        "agent_id": entry["agent_id"],
        "json_path": json_path,
        "token_path": token_path,
        "legacy_instances_file": LEGACY_INSTANCES_FILE,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
