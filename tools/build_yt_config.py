#!/usr/bin/env python3
"""
build_yt_config.py: derive Muffle's YouTube ad-removal data from uBlock Origin's list.

yt-main.js removes ads by deleting known field names (adPlacements, adSlots, playerAds)
from YouTube's player data and scrubbing them out of responses from known endpoints.
Those names come from uBlock Origin's filter list. This script reads the live list,
picks the YouTube rules, and writes a small JSON config the extension merges on top
of its built-in defaults:

    {"schema": 1, "keys": [...], "needles": [...], "unparsed": [...], ...}

  keys     JSON field names to delete / replace.  ^[A-Za-z][A-Za-z0-9_]{2,40}$, max 20
  needles  plain URL substrings that select which responses to scrub.  max 20
  unparsed YouTube rules the parser did not understand (informational)

The config is additive and contains no code or regexes, so a wrong line in the list can
at worst delete one more JSON field from player data.

Network and cosmetic YouTube rules go to tools/ubo-youtube-rules.txt, which
build_rules.py consumes like any other list.

Exit codes: 0 ok, 2 ok but the set of unparsed rules differs from the snapshot
(a technique the parser doesn't know appeared or disappeared), 1 error.

    python3 tools/build_yt_config.py --out yt-config.json          # from the live list
    python3 tools/build_yt_config.py --input file.txt --print      # from a local file
    python3 tools/build_yt_config.py --write-snapshot              # refresh the baseline
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
CACHE = os.path.join(TOOLS, ".cache")
UBO_URL = "https://ublockorigin.github.io/uAssets/filters/filters.txt"
SNAPSHOT = os.path.join(TOOLS, "ubo-youtube-snapshot.txt")
RULES_OUT = os.path.join(TOOLS, "ubo-youtube-rules.txt")
CACHE_TTL = 6 * 3600

SCHEMA = 1
DEFAULT_KEYS = ["adPlacements", "adSlots", "playerAds"]
DEFAULT_NEEDLES = ["/youtubei/v1/player", "/youtubei/v1/get_watch", "/playlist?list=", "/watch?"]
MAX_KEYS = 20
MAX_NEEDLES = 20
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,40}$")
RESERVED = {"important", "undefined", "null", "true", "false", "stack", "json"}   # scriptlet keywords, never field names
YT_DOMAIN_RE = re.compile(r"^[a-z0-9.,-]*youtube[a-z0-9.,-]*(##|\$)|^\|\|(www\.|m\.)?youtube\.com|^\|\|googlevideo|^\*_ad_")
SCRIPTLET_RE = re.compile(r"^([a-z0-9.,-]+)##\+js\((.*)\)\s*$")
ENDPOINT_ALIASES = {
    "player": "/youtubei/v1/player", "player?": "/youtubei/v1/player",
    "get_watch": "/youtubei/v1/get_watch", "get_watch?": "/youtubei/v1/get_watch",
    "next": "/youtubei/v1/next", "next?": "/youtubei/v1/next",
}


def fetch(url, refresh=False):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "ubo_filters.txt")
    if not refresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": "Muffle-rules-builder/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read().decode("utf-8", "replace")
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return data


# --------------------------------------------------------------------------- selection

def is_youtube_rule(line):
    if not line or line[0] in "![":
        return False
    if not YT_DOMAIN_RE.search(line):
        return False
    if "domain=" in line and "domain=youtube.com" not in line:
        return False
    return True


def youtube_rules(text):
    return [l.strip() for l in text.splitlines() if is_youtube_rule(l.strip())]


def last_modified(text):
    m = re.search(r"^! Last modified:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------- parsing

def split_args(body):
    """uBlock scriptlet args: comma separated, '\\,' is a literal comma."""
    parts = re.split(r"(?<!\\),", body)
    return [p.strip().replace("\\,", ",") for p in parts]


def unquote(s):
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def valid_needle(n):
    return (isinstance(n, str) and 4 <= len(n) <= 80 and all(33 <= ord(c) <= 126 for c in n)
            and ("/" in n or "?" in n))


def key_from_replace_arg(arg):
    """'"adPlacements"' -> adPlacements; /"adPlacements.*?…/ -> adPlacements."""
    a = unquote(arg)
    m = re.match(r'^/?"([A-Za-z0-9_]+)"?', a)
    return m.group(1) if m and KEY_RE.match(m.group(1)) else None


def needle_from_url_arg(arg):
    a = unquote(arg)
    if not a or a.startswith("/") and (a.endswith("/") or re.search(r"/[a-z]*$", a)):
        return None                      # regex: covered by built-in needles
    if a in ENDPOINT_ALIASES:
        return ENDPOINT_ALIASES[a]
    return a if valid_needle(a) else None


class Result:
    def __init__(self):
        self.keys = list(DEFAULT_KEYS)
        self.needles = list(DEFAULT_NEEDLES)
        self.unparsed = []
        self.network = []
        self.parsed = 0

    def add_key(self, k):
        if k and KEY_RE.match(k) and k not in RESERVED and k not in self.keys and len(self.keys) < MAX_KEYS:
            self.keys.append(k)

    def add_needle(self, n):
        if n and valid_needle(n) and n not in self.needles and len(self.needles) < MAX_NEEDLES:
            self.needles.append(n)


def handle_scriptlet(name, args, res):
    if name in ("set", "set-constant") and len(args) >= 2:
        m = re.match(r"^(?:ytInitialPlayerResponse|playerResponse)\.([A-Za-z0-9_]+)$", args[0])
        if m and args[1] == "undefined":
            res.add_key(m.group(1))
            return True
        return False
    if name.startswith("trusted-replace-") and name.endswith("-response") and args:
        key = key_from_replace_arg(args[0])
        if not key:
            return False
        res.add_key(key)
        if len(args) >= 3:
            res.add_needle(needle_from_url_arg(args[2]))
        return True
    if name == "json-prune" and args:
        for path in args[0].split():
            m = re.match(r"^(?:playerResponse\.)?([A-Za-z0-9_]+)$", path)
            if m and m.group(1) not in RESERVED:
                res.add_key(m.group(1))
        return True
    return False


def handle_network(line, res):
    m = re.search(r"replace=/\"([A-Za-z0-9_]+)\"/", line)
    if m:
        res.add_key(m.group(1))
        p = re.match(r"^\|\|(?:www\.|m\.)?youtube\.com(/[^$*]*)", line)
        if p:
            res.add_needle(p.group(1).rstrip("?") if p.group(1).endswith("?") and "=" not in p.group(1) else p.group(1))
        return True
    res.network.append(line)          # plain block/allow/cosmetic rule: the content blocker can take it
    return True


def parse(text):
    res = Result()
    for line in youtube_rules(text):
        m = SCRIPTLET_RE.match(line)
        if m:
            args = split_args(m.group(2))
            ok = handle_scriptlet(args[0], args[1:], res) if args else False
        elif "##" in line or line.startswith("||") or line.startswith("@@") or line.startswith("*"):
            ok = handle_network(line, res)
        else:
            ok = False
        if ok:
            res.parsed += 1
        else:
            res.unparsed.append(line)
    res.unparsed.sort()
    return res


def config_dict(res, source_text):
    return {
        "schema": SCHEMA,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "uboLastModified": last_modified(source_text),
        "keys": res.keys,
        "needles": res.needles,
        "unparsed": res.unparsed,
    }


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="read this file instead of fetching the uBlock list")
    ap.add_argument("--refresh", action="store_true", help="ignore the download cache")
    ap.add_argument("--out", help="write the JSON config here")
    ap.add_argument("--rules-out", default=RULES_OUT, help="write network/cosmetic rules here (default tools/ubo-youtube-rules.txt)")
    ap.add_argument("--snapshot", default=SNAPSHOT, help="baseline of YouTube rules (default tools/ubo-youtube-snapshot.txt)")
    ap.add_argument("--write-snapshot", action="store_true", help="overwrite the snapshot with the current YouTube rules")
    ap.add_argument("--print", action="store_true", help="print the config")
    args = ap.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = fetch(UBO_URL, args.refresh)

    res = parse(text)
    cfg = config_dict(res, text)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    if args.rules_out:
        with open(args.rules_out, "w", encoding="utf-8") as f:
            f.write("! YouTube network and cosmetic rules extracted from uBlock Origin's list by tools/build_yt_config.py.\n"
                    "! Generated file; do not edit. Source: %s\n" % UBO_URL)
            f.write("\n".join(res.network) + "\n")
    if args.write_snapshot:
        with open(args.snapshot, "w", encoding="utf-8") as f:
            f.write("! YouTube rules from uBlock Origin's list (%s), selected by tools/build_yt_config.py.\n"
                    "! Baseline for the weekly check: rules the parser cannot interpret are compared against this file.\n"
                    % (last_modified(text) or "unknown date"))
            f.write("\n".join(youtube_rules(text)) + "\n")

    print(f"youtube config: {len(res.keys)} keys {res.keys}, {len(res.needles)} needles, "
          f"{res.parsed} rules understood, {len(res.unparsed)} not, {len(res.network)} passed to the content blocker")
    if args.print:
        print(json.dumps(cfg, indent=2))

    rc = 0
    if os.path.exists(args.snapshot) and not args.write_snapshot:
        with open(args.snapshot, encoding="utf-8", errors="replace") as f:
            base = parse(f.read())
        new = sorted(set(res.unparsed) - set(base.unparsed))
        gone = sorted(set(base.unparsed) - set(res.unparsed))
        if new or gone:
            rc = 2
            for l in new:
                print("  NEW unparsed rule: " + l)
            for l in gone:
                print("  unparsed rule gone: " + l)
        else:
            print("youtube config: unparsed rules unchanged since the snapshot")
    return rc


if __name__ == "__main__":
    sys.exit(main())
