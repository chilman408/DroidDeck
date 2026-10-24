import contextlib
import io
import json
import unittest
from unittest.mock import patch

from tools.preflight import assess_host, collect_host_facts, main


GIB = 1024**3


def healthy_facts():
    return {
        "kvm_usable": True,
        "cpu_virtualization": True,
        "bridges": ["br0"],
        "ram_total_bytes": 128 * GIB,
        "ram_available_bytes": 100 * GIB,
        "build_free_bytes": 800 * GIB,
    }


class AssessHostTests(unittest.TestCase):
    def test_unusable_kvm_blocks_build(self):
        facts = healthy_facts()
        facts["kvm_usable"] = False

        result = assess_host(facts)

        self.assertTrue(any("KVM" in issue for issue in result["blocking"]))

    def test_insufficient_build_resources_warn_with_measured_values(self):
        facts = healthy_facts()
        facts["build_free_bytes"] = 300 * GIB
        facts["ram_total_bytes"] = 32 * GIB

        result = assess_host(facts)

        self.assertTrue(any("300" in issue and "400" in issue for issue in result["warnings"]))
        self.assertTrue(any("32" in issue and "64" in issue for issue in result["warnings"]))

    def test_low_available_ram_warns_even_when_total_ram_is_sufficient(self):
        facts = healthy_facts()
        facts["ram_total_bytes"] = 90 * GIB
        facts["ram_available_bytes"] = 9 * GIB

        result = assess_host(facts)

        self.assertTrue(any("9" in issue and "32" in issue for issue in result["warnings"]))
        self.assertTrue(any("8 GiB VM" in issue and "16" in issue for issue in result["warnings"]))

    def test_usable_kvm_and_bridge_do_not_block(self):
        result = assess_host(healthy_facts())

        self.assertEqual([], result["blocking"])

    def test_build_only_skips_vm_host_checks_but_keeps_capacity_warnings(self):
        facts = healthy_facts()
        facts.update(kvm_usable=False, cpu_virtualization=False, bridges=[], build_free_bytes=186 * GIB)

        result = assess_host(facts, require_vm_host=False)

        self.assertEqual([], result["blocking"])
        self.assertTrue(any("186" in issue and "400" in issue for issue in result["warnings"]))

    def test_missing_lan_bridge_blocks_vm_networking(self):
        facts = healthy_facts()
        facts["bridges"] = []

        result = assess_host(facts)

        self.assertTrue(any("bridge" in issue.lower() for issue in result["blocking"]))

    def test_collector_reports_required_host_facts(self):
        facts = collect_host_facts()

        self.assertIn("kvm_usable", facts)
        self.assertIn("cpu_virtualization", facts)
        self.assertIn("bridges", facts)
        self.assertIn("ram_total_bytes", facts)
        self.assertIn("build_free_bytes", facts)
        self.assertGreater(facts["build_free_bytes"], 0)

    def test_json_cli_reports_measured_facts_and_assessment(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main(["--json"])

        report = json.loads(output.getvalue())
        self.assertIn(exit_code, (0, 2))
        self.assertIn("facts", report)
        self.assertIn("blocking", report)
        self.assertIn("warnings", report)

    def test_build_only_cli_does_not_report_vm_host_blockers(self):
        facts = healthy_facts()
        facts.update(kvm_usable=False, cpu_virtualization=False, bridges=[])
        output = io.StringIO()

        with patch("tools.preflight.collect_host_facts", return_value=facts), contextlib.redirect_stdout(output):
            exit_code = main(["--build-only", "--json"])

        self.assertEqual(0, exit_code)
        self.assertEqual([], json.loads(output.getvalue())["blocking"])


if __name__ == "__main__":
    unittest.main()
