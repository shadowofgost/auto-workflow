"""Host-network evidence and scoped recovery for the Azure WARP trial."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def run(*args, input=None):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f"Network command failed: {args[0]} {args[1:3]}: {result.stderr[-300:]}")
    return result.stdout


def stable(value):
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items()
                if key not in {"expires", "cache", "handle", "packets", "bytes", "lastuse"}}
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def encoded(value):
    return json.dumps(stable(value), sort_keys=True)


def table_id(value):
    return {"main": "254", "local": "255", "default": "253"}.get(str(value), str(value))


def firewall_text(text):
    return "\n".join(re.sub(r"\[\d+:\d+\]", "[0:0]", line)
                     for line in text.splitlines() if line and not line.startswith("#"))


def snapshot(ssh_peer=None):
    result = {}
    for family in (4, 6):
        prefix = ["ip", f"-{family}"]
        result[f"rules{family}"] = json.loads(run(*prefix, "-j", "rule", "show"))
        result[f"routes{family}"] = json.loads(run(*prefix, "-j", "route", "show", "table", "all"))
        result[f"rule_text{family}"] = run(*prefix, "rule", "show").splitlines()
        result[f"route_text{family}"] = run(*prefix, "route", "show", "table", "all").splitlines()
    result["links"] = [{key: link.get(key) for key in ("ifname", "link_type", "linkinfo")}
                       for link in json.loads(run("ip", "-d", "-j", "link", "show"))]
    result["dns"] = hashlib.sha256(Path("/etc/resolv.conf").read_bytes()).hexdigest()
    result["dns_text"] = Path("/etc/resolv.conf").read_text()
    result["iptables"] = firewall_text(run("iptables-save"))
    result["ip6tables"] = firewall_text(run("ip6tables-save"))
    result["nft"] = json.loads(run("nft", "-j", "list", "ruleset"))
    result["destinations"] = ["1.1.1.1", "169.254.169.254"]
    if ssh_peer and ssh_peer not in result["destinations"]:
        result["destinations"].append(ssh_peer)
    result["unmarked"] = {}
    for destination in result["destinations"]:
        try:
            route = json.loads(run("ip", "-j", "route", "get", destination))[0]
            result["unmarked"][destination] = {key: route.get(key) for key in ("gateway", "dev", "prefsrc", "table", "type")}
            result["unmarked"][destination]["table"] = table_id(route.get("table", "main"))
        except RuntimeError:
            result["unmarked"][destination] = {"unreachable": True}
    return result


def regressions(before, after):
    problems = []
    for family in (4, 6):
        main = lambda state: sorted(encoded(route) for route in state[f"routes{family}"]
                                    if table_id(route.get("table", "main")) == "254")
        if main(before) != main(after):
            problems.append(f"IPv{family} main routing table changed")
        original = {encoded(rule) for rule in before[f"rules{family}"]}
        current = {encoded(rule) for rule in after[f"rules{family}"]}
        if original - current:
            problems.append(f"IPv{family} baseline policy rule removed or changed")
        for rule in after[f"rules{family}"]:
            if encoded(rule) not in original and not rule.get("fwmark"):
                problems.append(f"IPv{family} non-fwmark policy rule added")
    original_links = {link["ifname"] for link in before["links"]}
    for link in after["links"]:
        if link["ifname"] not in original_links:
            kind = (link.get("linkinfo") or {}).get("info_kind")
            if kind in ("tun", "tap", "wireguard") or "warp" in link["ifname"].lower():
                problems.append(f"Unexpected tunnel interface: {link['ifname']}")
    if not original_links.issubset({link["ifname"] for link in after["links"]}):
        problems.append("Baseline network interface disappeared")
    for field in ("dns", "unmarked", "iptables", "ip6tables"):
        if before[field] != after[field]:
            problems.append(f"Host {field} changed")
    nft = lambda state: [encoded(item) for item in state["nft"]["nftables"] if "metainfo" not in item]
    if nft(before) != nft(after):
        problems.append("Host nftables ruleset changed")
    return problems


def summary(before, after):
    return {"problems": regressions(before, after),
            "added_rules": {str(family): [rule for rule in after[f"rules{family}"]
                                         if encoded(rule) not in {encoded(r) for r in before[f"rules{family}"]}]
                            for family in (4, 6)},
            "unmarked": after["unmarked"], "interfaces": [link["ifname"] for link in after["links"]]}


def rule_tokens(line):
    priority, body = line.split(":", 1)
    return ["priority", priority.strip(), *shlex.split(body)]


def route_tokens(line):
    tokens = shlex.split(line)
    result = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "expires":
            index += 2
            continue
        if token not in ("linkdown", "cache"):
            result.append(token)
        index += 1
    return result


def restore_firewall(before, after):
    """Restore changed nft tables/chains only; never flush the whole ruleset."""
    old = [item for item in before["nft"]["nftables"] if "metainfo" not in item]
    new = [item for item in after["nft"]["nftables"] if "metainfo" not in item]
    if [encoded(item) for item in old] == [encoded(item) for item in new]:
        return
    table_key = lambda obj: (obj["family"], obj.get("table", obj.get("name")))
    old_tables = {table_key(item["table"]) for item in old if "table" in item}
    new_tables = {table_key(item["table"]) for item in new if "table" in item}
    commands = []
    for family, name in new_tables - old_tables:
        commands.append({"delete": {"table": {"family": family, "name": name}}})
    for family, name in old_tables:
        select = lambda items: [item for item in items if table_key(next(iter(item.values()))) == (family, name)]
        old_items, new_items = select(old), select(new)
        if [encoded(item) for item in old_items] == [encoded(item) for item in new_items]:
            continue
        # Rebuild only this changed table, preserving all other tables verbatim.
        if (family, name) in new_tables:
            commands.append({"delete": {"table": {"family": family, "name": name}}})
        for item in old_items:
            clean = copy.deepcopy(item)
            for obj in clean.values():
                obj.pop("handle", None)
            commands.append({"add": clean})
    if commands:
        run("nft", "-j", "-f", "-", input=json.dumps({"nftables": commands}))


def restore(before):
    """Undo the measured test delta, including changed baseline route entries."""
    current = snapshot(next((d for d in before["destinations"] if d not in ("1.1.1.1", "169.254.169.254")), None))
    actions = []
    for family in (4, 6):
        old_rules = {tuple(rule_tokens(line)) for line in before[f"rule_text{family}"]}
        now_rules = {tuple(rule_tokens(line)) for line in current[f"rule_text{family}"]}
        for tokens in now_rules - old_rules:
            run("ip", f"-{family}", "rule", "del", *tokens)
            actions.append(f"IPv{family} removed policy rule {tokens}")
        for tokens in old_rules - now_rules:
            run("ip", f"-{family}", "rule", "add", *tokens)
            actions.append(f"IPv{family} restored baseline policy rule {tokens}")
        old_routes = {tuple(route_tokens(line)) for line in before[f"route_text{family}"]}
        now_routes = {tuple(route_tokens(line)) for line in current[f"route_text{family}"]}
        for tokens in now_routes - old_routes:
            run("ip", f"-{family}", "route", "del", *tokens)
            actions.append(f"IPv{family} removed added route {tokens}")
        for tokens in old_routes - now_routes:
            run("ip", f"-{family}", "route", "replace", *tokens)
            actions.append(f"IPv{family} restored baseline route {tokens}")
    restore_firewall(before, current)
    if current["dns"] != before["dns"]:
        Path("/etc/resolv.conf").write_text(before["dns_text"])
        actions.append("Restored resolver configuration")
    old_links = {link["ifname"] for link in before["links"]}
    for link in current["links"]:
        if link["ifname"] not in old_links and ("warp" in link["ifname"].lower() or
                (link.get("linkinfo") or {}).get("info_kind") in ("tun", "tap", "wireguard")):
            run("ip", "link", "delete", "dev", link["ifname"])
            actions.append(f"Removed trial tunnel interface {link['ifname']}")
    return actions


def daemon_capabilities(rows, proc=Path("/proc")):
    found = []
    for row in rows:
        if Path(row.get("command", "").split()[0]).name != "warp-svc":
            continue
        pid = int(row["pid"])
        try:
            status = (proc / str(pid) / "status").read_text()
        except FileNotFoundError:
            continue
        effective = re.search(r"^CapEff:\s*([0-9a-f]+)", status, re.MULTILINE)
        found.append({"pid": pid, "cap_eff": effective.group(1) if effective else None,
                      "net_admin": bool(int(effective.group(1), 16) & (1 << 12)) if effective else None})
    return found


def compose_other_services(text):
    import yaml
    document = yaml.compose(text)
    services = next(value for key, value in document.value if key.value == "services")
    key, node = next((key, value) for key, value in services.value if key.value == "warp")
    lines = text.splitlines(keepends=True)
    return "".join(lines[:key.start_mark.line] + lines[node.end_mark.line:])


def update_compose(text, image, net_admin):
    """Rewrite the WARP node only, keeping other services and comments intact."""
    import yaml
    document = yaml.compose(text)
    services = next(value for key, value in document.value if key.value == "services")
    key, node = next((key, value) for key, value in services.value if key.value == "warp")
    service = yaml.safe_load(text)["services"]["warp"]
    service["image"] = image
    if net_admin:
        caps = service.setdefault("cap_add", [])
        if not any(str(cap).removeprefix("CAP_").upper() == "NET_ADMIN" for cap in caps):
            caps.append("NET_ADMIN")
    service["logging"] = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    rendered = yaml.safe_dump({"warp": service}, sort_keys=False, allow_unicode=True)
    replacement = "".join(" " * key.start_mark.column + line + "\n" for line in rendered.splitlines())
    lines = text.splitlines(keepends=True)
    return "".join(lines[:key.start_mark.line]) + replacement + "".join(lines[node.end_mark.line:])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True, help="Read-only, consecutive baseline validation")
    parser.parse_args()
    before = snapshot()
    after = snapshot()
    result = summary(before, after)
    print(json.dumps(result))
    raise SystemExit(1 if result["problems"] else 0)
