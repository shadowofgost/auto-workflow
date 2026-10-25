import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("azure_network_test", Path(__file__).parents[1] / "azure_network.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class NetworkTests(unittest.TestCase):
    def baseline(self):
        return {"rules4": [{"priority": 0, "table": "local"}], "rules6": [],
                "routes4": [{"dst": "default", "gateway": "10.0.0.1", "dev": "eth0"}], "routes6": [],
                "links": [{"ifname": "lo"}, {"ifname": "eth0"}], "dns": "same",
                "unmarked": {"1.1.1.1": {"gateway": "10.0.0.1", "dev": "eth0"}},
                "iptables": "unchanged", "ip6tables": "unchanged", "nft": {"nftables": []}}

    def test_marked_rule_is_recorded_without_allowing_default_route_changes(self):
        before = self.baseline()
        after = copy.deepcopy(before)
        after["rules4"].append({"priority": 100, "fwmark": "0x100cf", "table": "main"})
        self.assertEqual(module.regressions(before, after), [])
        self.assertEqual(len(module.summary(before, after)["added_rules"]["4"]), 1)
        after["routes4"][0]["gateway"] = "192.0.2.1"
        self.assertIn("IPv4 main routing table changed", module.regressions(before, after))

    def test_unmarked_rule_tunnel_dns_and_route_regressions_are_detected(self):
        for change in (lambda state: state["rules4"].append({"priority": 99, "table": 100}),
                       lambda state: state["links"].append({"ifname": "CloudflareWARP"}),
                       lambda state: state.update(dns="changed"),
                       lambda state: state.update(unmarked={})):
            before = self.baseline()
            after = copy.deepcopy(before)
            change(after)
            self.assertTrue(module.regressions(before, after))

    def test_volatile_firewall_counters_do_not_cause_false_failures(self):
        self.assertEqual(module.firewall_text("# timestamp\n[0:0] -A INPUT\n"),
                         module.firewall_text("# different\n[100:999] -A INPUT\n"))
        self.assertEqual(module.encoded({"counter": {"packets": 1, "bytes": 10}}),
                         module.encoded({"counter": {"packets": 9, "bytes": 99}}))

    def test_daemon_capability_is_not_confused_with_unprivileged_pid1(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, caps in (("10", "00000000"), ("11", "00001000")):
                (root / pid).mkdir()
                (root / pid / "status").write_text("CapEff:\t" + caps + "\n")
            actual = module.daemon_capabilities([{"pid": 10, "command": "bash"},
                                                 {"pid": 11, "command": "/usr/bin/warp-svc"}], root)
            self.assertEqual(actual, [{"pid": 11, "cap_eff": "00001000", "net_admin": True}])

    def test_compose_change_is_idempotent_and_preserves_other_services(self):
        source = "services:\n    warp:\n        image: old\n        network_mode: host\n        cap_add: [NET_RAW]\n    nginx:\n        image: nginx\n        # keep nginx settings\n        restart: always\n"
        revised = module.update_compose(source, "tested@sha256:digest", True)
        self.assertEqual(module.update_compose(revised, "tested@sha256:digest", True), revised)
        self.assertEqual(revised.split("    nginx:\n")[1], source.split("    nginx:\n")[1])
        self.assertIn("NET_RAW", revised)
        self.assertEqual(revised.count("NET_ADMIN"), 1)

    def test_firewall_recovery_is_scoped_to_changed_tables(self):
        before = {"nft": {"nftables": [{"table": {"family": "ip", "name": "docker"}}]}}
        after = copy.deepcopy(before)
        after["nft"]["nftables"].append({"table": {"family": "ip", "name": "warp-test"}})
        with patch.object(module, "run") as run:
            module.restore_firewall(before, after)
        commands = json.loads(run.call_args.kwargs["input"])["nftables"]
        self.assertEqual(commands, [{"delete": {"table": {"family": "ip", "name": "warp-test"}}}])


if __name__ == "__main__":
    unittest.main()
