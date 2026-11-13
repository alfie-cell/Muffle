#!/usr/bin/env python3
"""
build_rules.py: turn Adblock Plus style filter lists into a Safari content blocker list.

Standard library only. Run from anywhere:

    python3 tools/build_rules.py            # uses cached downloads if < 1 day old
    python3 tools/build_rules.py --refresh  # re-download the lists
    python3 tools/build_rules.py --stats    # print detailed skip statistics

Design choices (see README.md):
  * Network rules from every list are converted.
  * Domain-specific cosmetic rules (example.com##.ad) are converted and merged.
  * Generic cosmetic rules (##.ad) are dropped on purpose. Safari would inject
    them into every page, which is the single biggest performance cost of
    EasyList on Safari.
  * Anything the converter does not fully understand is skipped, never guessed.
    Safari refuses the whole list if a single rule is invalid, so caution wins.

Safari regex notes (WebKit content extensions), which drive the conversion:
  * Supported: '.', '*', '+', '?', character classes incl. negated, groups.
  * Not supported: alternation '|', '$' anywhere but the end, back references,
    look-around, counted quantifiers, non-ASCII.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
CACHE = os.path.join(TOOLS, ".cache")
OUT = os.path.join(ROOT, "Blocker", "blockerList.json")
META = os.path.join(ROOT, "Blocker", "rules-meta.json")
SOURCES = os.path.join(TOOLS, "sources.txt")
EXTRA = os.path.join(TOOLS, "extra_rules.txt")

MAX_RULES = 150_000          # Safari's per-content-blocker limit

# Profiles pick sections of the lists ("! *** easylist:easylist/easylist_adservers.txt ***"
# markers). A pattern list is matched with re.search against the section name; rules that
# appear before any marker are always included. None means "take the whole list".
PETER_LOWE = "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=adblockplus&showintro=0&mimetype=plaintext"
PROFILES = {
    # Everything the converter can express. ~120k rules, ~45 MB compiled.
    "full": {"sources": None, "include": None, "exclude": []},
    # Network blocking from every general/third-party/ad-server section; no per-site
    # cosmetics, no CNAME-cloaking long tail, no international-only sections.
    "lean": {"sources": None, "include": None,
             "exclude": [r"hide", r"cname", r"international", r"specific_perimeterx",
                         r"_uBO", r"_abp", r"emailtrackers", r"notifications", r"mining", r"admiral"]},
    # Smallest useful set: generic patterns + third-party rules from EasyList/EasyPrivacy,
    # with Peter Lowe's curated ad/tracking server list instead of EasyList's 49k ad servers.
    "minimal": {"sources": [PETER_LOWE],
                "include": [r"general_block", r"thirdparty(?!_international)", r"allowlist(?!_general_hide)",
                            r"easyprivacy_general\.txt", r"trackingservers_general", r"trackingservers_thirdparty",
                            r"easyprivacy_allowlist\.txt", r"easyprivacy_thirdparty\.txt"],
                "exclude": [r"hide", r"cname", r"international", r"adult", r"dimensions"]},
}
SECTION_RE = re.compile(r"^!\s*\*\*\*\s*(\S+)\s*\*\*\*")
CACHE_TTL = 24 * 3600
SELECTORS_PER_RULE = 40      # cosmetic selectors merged into one rule

ALL_TYPES = ["document", "image", "style-sheet", "script", "font", "raw",
             "svg-document", "media", "popup", "fetch", "websocket", "ping"]
TYPE_MAP = {
    "script": ["script"], "image": ["image"],
    "stylesheet": ["style-sheet"], "css": ["style-sheet"],
    "font": ["font"], "media": ["media"], "popup": ["popup"],
    "xmlhttprequest": ["raw", "fetch"], "xhr": ["raw", "fetch"],
    "websocket": ["websocket"], "ping": ["ping"], "beacon": ["ping"],
    "other": ["raw"],
    "document": ["document"], "doc": ["document"],
    "subdocument": ["document"], "frame": ["document"],
}
TOP_FRAME = {"document", "doc"}
CHILD_FRAME = {"subdocument", "frame"}
IGNORED_OPTIONS = {"important"}          # option dropped, rule kept
SEP = "[^-_.%a-z0-9]"                    # ABP '^' separator
HOST_RE = re.compile(r"^[a-z0-9.-]+$")
COSMETIC_RE = re.compile(r"^([^#]*)#([@?$%]*)#(.+)$")
SAFE_PSEUDO = re.compile(
    r":(?:not\(|nth-child\(|nth-last-child\(|nth-of-type\(|nth-last-of-type\("
    r"|first-child|last-child|only-child|first-of-type|last-of-type|only-of-type|empty|root)")
VALID_ACTIONS = {"block", "block-cookies", "css-display-none", "ignore-previous-rules", "make-https"}


class Skip(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------- fetch

def fetch(url, refresh):
    if not url.startswith("http"):
        # A local list (e.g. tools/ubo-youtube-rules.txt written by build_yt_config.py).
        local = os.path.join(ROOT, url)
        if not os.path.exists(local):
            print(f"  (local list {url} not found; skipped)")
            return ""
        with open(local, encoding="utf-8", errors="replace") as f:
            return f.read()
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, re.sub(r"[^a-z0-9.]+", "_", url.lower()))
    if not refresh and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": "Muffle-rules-builder/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read().decode("utf-8", "replace")
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return data


# --------------------------------------------------------------------------- network rules

def pattern_to_regex(pattern):
    p = pattern.lower()
    if p in ("", "*"):
        return ".*"
    if any(ord(c) > 127 for c in p):
        raise Skip("non-ascii")
    if len(p) > 1 and p.startswith("/") and p.endswith("/"):
        raise Skip("regex-filter")
    out = []
    if p.startswith("||"):
        out.append(r"^[^:]+:(//)?([^/]+\.)?")
        p = p[2:]
    elif p.startswith("|"):
        out.append("^")
        p = p[1:]
    anchored_end = p.endswith("|")
    if anchored_end:
        p = p[:-1]
    p = re.sub(r"\*+", "*", p).strip("*")
    if not p:
        raise Skip("empty-pattern")
    for c in p:
        if c == "*":
            out.append(".*")
        elif c == "^":
            # ABP separator. WebKit has no alternation, so the "end of URL"
            # case cannot be expressed; a URL always has "/" after the host,
            # which is where almost every '^' sits.
            out.append(SEP)
        elif c == "|":
            raise Skip("pipe-in-pattern")
        elif c in ".+?()[]{}\\$/":
            out.append("\\" + c)
        else:
            out.append(c)
    if anchored_end:
        out.append("$")
    regex = "".join(out)
    try:
        re.compile(regex)
    except re.error:
        raise Skip("bad-regex")
    return regex


def parse_options(opt_text):
    types, neg_types = set(), set()
    top = child = False
    party = None
    domains = []
    case_sensitive = False
    for raw in opt_text.split(","):
        opt = raw.strip()
        if not opt:
            continue
        neg = opt.startswith("~")
        name = opt[1:] if neg else opt
        low = name.lower()
        if low in ("third-party", "3p"):
            party = "first-party" if neg else "third-party"
        elif low in ("first-party", "1p"):
            party = "third-party" if neg else "first-party"
        elif low.startswith("domain="):
            domains = name[7:].split("|")
        elif low == "match-case":
            case_sensitive = True
        elif low == "all" and not neg:
            pass
        elif low in IGNORED_OPTIONS:
            pass
        elif low in TYPE_MAP:
            (neg_types if neg else types).update(TYPE_MAP[low])
            if not neg:
                top = top or low in TOP_FRAME
                child = child or low in CHILD_FRAME
        else:
            raise Skip("option:" + low)
    if types and neg_types:
        raise Skip("mixed-type-negation")
    resource_types = None
    if types:
        resource_types = [t for t in ALL_TYPES if t in types]
    elif neg_types:
        resource_types = [t for t in ALL_TYPES if t not in neg_types]
    load_context = None
    if resource_types == ["document"] and top != child:
        load_context = ["top-frame"] if top else ["child-frame"]
    return resource_types, load_context, party, domains, case_sensitive


def split_domains(domains, allow_mixed=False):
    inc, exc = [], []
    for d in domains:
        d = d.strip().lower()
        if not d:
            continue
        neg = d.startswith("~")
        d = d[1:] if neg else d
        if not HOST_RE.match(d) or d.endswith("."):
            raise Skip("bad-domain")
        (exc if neg else inc).append("*" + d)
    if inc and exc and not allow_mixed:
        raise Skip("mixed-domain")
    return sorted(set(inc)), sorted(set(exc))


def convert_network(line):
    exception = line.startswith("@@")
    if exception:
        line = line[2:]
    if "$" in line:
        pattern, options = line.rsplit("$", 1)
    else:
        pattern, options = line, ""
    if len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
        raise Skip("regex-filter")
    resource_types, load_context, party, domains, case_sensitive = parse_options(options)
    inc, exc = split_domains(domains)

    trigger = {}
    whole_site = False
    if exception and resource_types == ["document"] and load_context == ["top-frame"] and not inc and not exc:
        # "@@||example.com^$document": switch blocking off on example.com pages.
        host = pattern.lower()
        host = host[2:] if host.startswith("||") else host
        host = host.rstrip("|").rstrip("^")
        if HOST_RE.match(host) and "." in host:
            trigger = {"url-filter": ".*", "if-domain": ["*" + host]}
            whole_site = True
    if not whole_site:
        trigger["url-filter"] = pattern_to_regex(pattern)
        if case_sensitive:
            trigger["url-filter-is-case-sensitive"] = True
        if resource_types:
            trigger["resource-type"] = resource_types
        if load_context:
            trigger["load-context"] = load_context
        if party:
            trigger["load-type"] = [party]
        if inc:
            trigger["if-domain"] = inc
        if exc:
            trigger["unless-domain"] = exc
    action = {"type": "ignore-previous-rules" if exception else "block"}
    return {"trigger": trigger, "action": action}, exception


# --------------------------------------------------------------------------- cosmetic rules

def selector_ok(sel):
    if not sel or len(sel) > 300 or any(ord(c) > 127 for c in sel):
        return False
    if sel.startswith("+js(") or "{" in sel or "}" in sel or "/*" in sel:
        return False
    stripped = re.sub(r"\[[^\]]*\]", "", sel)
    for m in re.finditer(":", stripped):
        if not SAFE_PSEUDO.match(stripped, m.start()):
            return False
    return True


def convert_cosmetic(m):
    domains, marker, selector = m.group(1), m.group(2), m.group(3).strip()
    if marker:
        kind = {"@": "exception", "?": "extended", "$": "snippet", "%": "script"}.get(marker[0], "other")
        raise Skip("cosmetic-" + kind)
    if not domains.strip():
        raise Skip("generic-cosmetic")
    inc, _exc = split_domains(domains.split(","), allow_mixed=True)
    if not inc:
        raise Skip("generic-cosmetic")
    if not selector_ok(selector):
        raise Skip("unsupported-selector")
    return inc, selector


# --------------------------------------------------------------------------- output

def validate(rule):
    t, a = rule["trigger"], rule["action"]
    assert isinstance(t.get("url-filter"), str) and t["url-filter"], rule
    re.compile(t["url-filter"])
    assert a["type"] in VALID_ACTIONS, rule
    assert not ("if-domain" in t and "unless-domain" in t), rule
    if a["type"] == "css-display-none":
        assert a.get("selector"), rule
    for k in ("resource-type", "load-type", "load-context", "if-domain", "unless-domain"):
        if k in t:
            assert isinstance(t[k], list) and t[k], rule


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-download filter lists")
    ap.add_argument("--stats", action="store_true", help="print every skip reason")
    ap.add_argument("--max", type=int, default=MAX_RULES, help="rule cap (default %(default)s)")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="lean", help="rule profile (default %(default)s)")
    ap.add_argument("--out", default=OUT, help="output path (default Blocker/blockerList.json)")
    args = ap.parse_args()
    profile = PROFILES[args.profile]
    out_path = args.out
    meta_path = META if args.out == OUT else os.path.splitext(args.out)[0] + "-meta.json"

    with open(SOURCES, encoding="utf-8") as f:
        sources = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    sources += profile["sources"] or []
    texts = []
    for url in sources:
        print(f"fetching {url}" if url.startswith("http") else f"reading {url}")
        texts.append((url, fetch(url, args.refresh)))
    if os.path.exists(EXTRA):
        with open(EXTRA, encoding="utf-8") as f:
            texts.append(("extra_rules.txt", f.read()))

    blocks, exceptions = [], []
    seen = set()
    cosmetic = defaultdict(set)      # selector -> {domains}
    skipped = Counter()
    per_source = {}
    include = [re.compile(p) for p in (profile["include"] or [])]
    exclude = [re.compile(p) for p in profile["exclude"]]
    sections_used, sections_dropped = set(), set()
    for name, text in texts:
        counts = Counter()
        section = ""
        take = True
        for raw in text.splitlines():
            line = raw.strip()
            m = SECTION_RE.match(line)
            if m:
                section = m.group(1)
                take = (not include or any(r.search(section) for r in include)) and \
                       not any(r.search(section) for r in exclude)
                (sections_used if take else sections_dropped).add(section)
                continue
            if not line or line[0] in "![" or (line.startswith("#") and not line.startswith("##")):
                continue
            if not take:
                counts["section-skipped"] += 1
                continue
            try:
                m = COSMETIC_RE.match(line)
                if m:
                    inc, selector = convert_cosmetic(m)
                    cosmetic[selector].update(inc)
                    counts["cosmetic"] += 1
                else:
                    rule, is_exc = convert_network(line)
                    key = json.dumps(rule, sort_keys=True)
                    if key in seen:
                        counts["duplicate"] += 1
                        continue
                    seen.add(key)
                    (exceptions if is_exc else blocks).append(rule)
                    counts["exception" if is_exc else "block"] += 1
            except Skip as s:
                skipped[s.reason] += 1
                counts["skipped"] += 1
        per_source[name] = dict(counts)

    # Merge cosmetics: identical domain sets share one rule with a selector list.
    by_domains = defaultdict(list)
    for selector, doms in cosmetic.items():
        by_domains[tuple(sorted(doms))].append(selector)
    css_rules = []
    for doms, selectors in sorted(by_domains.items()):
        selectors.sort()
        for i in range(0, len(selectors), SELECTORS_PER_RULE):
            css_rules.append({
                "trigger": {"url-filter": ".*", "if-domain": list(doms)},
                "action": {"type": "css-display-none",
                           "selector": ", ".join(selectors[i:i + SELECTORS_PER_RULE])},
            })

    # Order matters: ignore-previous-rules only cancels rules listed before it.
    rules = blocks + css_rules + exceptions
    if len(rules) > args.max:
        over = len(rules) - args.max
        print(f"WARNING: {len(rules)} rules exceed the cap of {args.max}; dropping {over} cosmetic rules")
        css_rules = css_rules[:max(0, len(css_rules) - over)]
        rules = blocks + css_rules + exceptions
    if len(rules) > args.max:
        keep = args.max - len(exceptions)
        print(f"WARNING: still over the cap; truncating block rules to {keep}")
        rules = blocks[:keep] + exceptions

    for r in rules:
        validate(r)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rules, f, separators=(",", ":"), ensure_ascii=True)
    meta = {
        "profile": args.profile,
        "count": len(rules),
        "block": len(blocks),
        "cosmetic": len(css_rules),
        "cosmeticSelectors": len(cosmetic),
        "exception": len(exceptions),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sources": sources,
        "skipped": dict(skipped.most_common()),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print()
    print(f"profile       : {args.profile}  (sections used {len(sections_used)}, dropped {len(sections_dropped)})")
    print(f"{'source':<50} {'block':>7} {'except':>7} {'cosm.':>7} {'dupes':>7} {'skip':>7} {'sect-':>7}")
    for name, c in per_source.items():
        short = name.replace("https://", "")[:50]
        print(f"{short:<50} {c.get('block', 0):>7} {c.get('exception', 0):>7} "
              f"{c.get('cosmetic', 0):>7} {c.get('duplicate', 0):>7} {c.get('skipped', 0):>7} {c.get('section-skipped', 0):>7}")
    print()
    print(f"rules written : {len(rules):>7}  -> {os.path.relpath(out_path, ROOT)}")
    print(f"  block       : {len(blocks):>7}")
    print(f"  cosmetic    : {len(css_rules):>7}  ({len(cosmetic)} selectors on {len(by_domains)} domain sets)")
    print(f"  exception   : {len(exceptions):>7}")
    print(f"size          : {os.path.getsize(out_path) / 1e6:.1f} MB")
    top = skipped.most_common(None if args.stats else 8)
    print("skipped       : " + ", ".join(f"{k} {v}" for k, v in top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
