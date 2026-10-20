"""Decorator collection and reporting, without pretending to run native physics."""

import inspect
import json
import tempfile
import unittest
from pathlib import Path

from oniharness import (
    Cell,
    DefinitionError,
    Region,
    Scenario,
    Suite,
    TestFailure,
    TestReport,
    __version__,
    expect,
    parametrize,
)


class RecordingHarness:
    def __init__(self, failed_name=""):
        self.calls = []
        self.failed_name = failed_name

    def run_scenario(self, scenario, *, repeat, timeout, check):
        self.calls.append((scenario, repeat, timeout, check))
        failed = bool(self.failed_name and self.failed_name in scenario["name"])
        tests = [
            {
                "name": scenario["name"],
                "status": "failed" if failed else "passed",
                "evidence": "backend-fixture",
                "error": "fixture failure" if failed else None,
            }
            for _ in range(repeat)
        ]
        return TestReport(
            {"schemaVersion": 1, "harnessVersion": "fixture", "tests": tests},
            1 if failed else 0,
            scenario["name"],
            "",
        )


class SuiteTests(unittest.TestCase):
    def test_decorators_are_lazy_preserve_function_and_expand_grid(self):
        backend = RecordingHarness()
        suite = Suite(backend)
        built = []

        @suite.scenario(repeat=2, timeout=30)
        @parametrize(temperature=[280, 300], mass=[1, 2])
        @expect.mass_conserved()
        @expect.cell_temperature(8, 8, min=270, max=320)
        def oxygen(temperature, mass):
            """Factory documentation."""
            built.append((temperature, mass))
            return Scenario(fill=Cell("Oxygen", mass=mass, temperature=temperature))

        self.assertEqual(built, [])
        self.assertEqual(backend.calls, [])
        self.assertEqual(oxygen.__name__, "oxygen")
        self.assertEqual(oxygen.__doc__, "Factory documentation.")
        self.assertEqual(
            list(inspect.signature(oxygen).parameters), ["temperature", "mass"]
        )
        report = suite.run()
        self.assertEqual(built, [(280, 1), (280, 2), (300, 1), (300, 2)])
        self.assertEqual(len(report.tests), 8)
        self.assertEqual(str(report), "PASS: 8/8 tests passed")
        self.assertEqual(report.data["pythonVersion"], __version__)
        first, repeat, timeout, check = backend.calls[0]
        self.assertEqual(first["name"], "oxygen[temperature=280, mass=1]")
        self.assertEqual(
            first["fill"], {"element": "Oxygen", "mass": 1, "temperature": 280}
        )
        self.assertEqual(
            {item["kind"] for item in first["assertions"]},
            {"massConserved", "cellTemperature"},
        )
        self.assertEqual((repeat, timeout, check), (2, 30, False))

    def test_any_decorator_order_and_stacked_parameters(self):
        backend = RecordingHarness()
        suite = Suite(backend)

        @expect.mass_conserved()
        @parametrize(temperature=(value for value in [280, 300]))
        @suite.scenario
        @parametrize(mass=[1, 2])
        def oxygen(temperature, mass):
            return Scenario(fill=Cell(mass=mass, temperature=temperature))

        self.assertEqual(len(suite.run().tests), 4)
        self.assertEqual(
            len(suite.run().tests), 4
        )  # generators were materialized at decoration
        self.assertEqual(len(backend.calls), 8)

    def test_case_filter_does_not_call_unselected_factories(self):
        backend = RecordingHarness()
        suite = Suite(backend)
        built = []

        @suite.scenario(name="oxygen")
        @parametrize(temperature=[280, 300])
        @expect.mass_conserved()
        def factory(temperature):
            built.append(temperature)
            return Scenario()

        report = suite.run(filter="TEMPERATURE=300")
        self.assertEqual(built, [300])
        self.assertEqual(report.tests[0]["name"], "oxygen[temperature=300]")
        with self.assertRaisesRegex(DefinitionError, "No matching"):
            suite.run(filter="missing")
        self.assertEqual(len(backend.calls), 1)

    def test_scenario_and_dict_bases_are_reusable_without_mutation(self):
        backend = RecordingHarness()
        suite = Suite(backend)
        base = Scenario().expect(expect.mass_conserved())
        original = {"fill": {"element": "Oxygen", "mass": 1, "temperature": 300}}

        @suite.scenario
        @expect.cell_mass(8, 8, min=0)
        def typed():
            return base

        @suite.scenario
        @expect.mass_conserved()
        def raw():
            return original

        suite.run()
        suite.run()
        self.assertEqual(len(base.assertions), 1)
        self.assertNotIn("assertions", original)
        self.assertNotIn("name", original)
        self.assertEqual(len(backend.calls[0][0]["assertions"]), 2)
        self.assertEqual(len(backend.calls[2][0]["assertions"]), 2)

    def test_failure_is_saved_and_later_cases_still_run(self):
        backend = RecordingHarness(failed_name="temperature=280")
        suite = Suite(backend)

        @suite.scenario
        @parametrize(temperature=[280, 300, 320])
        @expect.mass_conserved()
        def oxygen(temperature):
            return Scenario(fill=Cell(temperature=temperature))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results/report.json"
            with self.assertRaises(TestFailure) as caught:
                suite.run(report_path=output)
            self.assertEqual(len(backend.calls), 3)
            report = caught.exception.report
            self.assertFalse(report.passed)
            self.assertEqual(str(report), "FAIL: 2/3 tests passed")
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), report.data
            )
            invalid = TestReport({"tests": [object()]}, 1, "", "")
            with self.assertRaises(TypeError):
                invalid.save(output)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), report.data
            )
            self.assertEqual(list(output.parent.glob("*.tmp")), [])
            self.assertFalse(suite.run(check=False).passed)

    def test_invalid_definitions_fail_before_any_backend_run(self):
        for factory, expected in [
            (lambda: None, "Return a Scenario"),
            (lambda: Scenario(), "Add @expect"),
            (lambda: {"assertions": "invalid"}, "assertions must be a sequence"),
            (lambda: {"assertions": [object()]}, "not JSON serializable"),
        ]:
            with self.subTest(expected=expected):
                backend = RecordingHarness()
                suite = Suite(backend)

                @suite.scenario
                @expect.mass_conserved()
                def valid():
                    return Scenario()

                suite.scenario(factory, name="bad")
                with self.assertRaisesRegex(DefinitionError, expected):
                    suite.run()
                self.assertEqual(backend.calls, [])

    def test_parameter_signature_and_factory_exceptions_name_the_case(self):
        suite = Suite(RecordingHarness())

        @suite.scenario
        @parametrize(mass=[1])
        @expect.mass_conserved()
        def bad(temperature):
            return Scenario()

        with self.assertRaisesRegex(DefinitionError, r"bad\[mass=1\].*TypeError"):
            suite.run()

        suite = Suite(RecordingHarness())

        @suite.scenario
        def broken():
            raise RuntimeError("factory exploded")

        with self.assertRaisesRegex(
            DefinitionError, "broken: RuntimeError: factory exploded"
        ) as caught:
            suite.run()
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)

    def test_empty_duplicate_and_async_definitions_are_explicit_errors(self):
        suite = Suite(RecordingHarness())
        with self.assertRaisesRegex(DefinitionError, "Empty"):
            suite.run()
        for arguments in ({}, {"mass": []}, {"element": "Oxygen"}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parametrize(**arguments)

        def factory(mass=1):
            return Scenario().expect(expect.mass_conserved())

        parametrize(mass=[1])(factory)
        with self.assertRaisesRegex(ValueError, "Duplicate parameters"):
            parametrize(mass=[2])(factory)
        suite.scenario(factory)
        with self.assertRaisesRegex(ValueError, "already registered"):
            suite.scenario(factory)

        async def asynchronous():
            return Scenario()

        with self.assertRaisesRegex(TypeError, "synchronous"):
            suite.scenario(asynchronous)
        with self.assertRaisesRegex(ValueError, "requires a trusted checkout"):
            Suite(build=True)

    def test_typed_regions_and_all_expectations_match_native_json(self):
        base = Scenario(
            fill=Cell("Vacuum", mass=0),
            regions=(Region(2, 3, cell=Cell("Water", mass=1000)),),
        )
        checked = base.expect(
            expect.total_mass(min=0),
            expect.cell_mass(2, 3, min=100),
            expect.cell_temperature(2, 3, max=400),
        )
        self.assertEqual(base.assertions, ())
        data = checked.to_dict()
        self.assertEqual(
            data["regions"][0],
            {
                "x": 2,
                "y": 3,
                "width": 1,
                "height": 1,
                "cell": {"element": "Water", "mass": 1000, "temperature": 300},
            },
        )
        self.assertEqual(
            data["assertions"],
            [
                {"kind": "totalMass", "min": 0},
                {"kind": "cellMass", "min": 100, "x": 2, "y": 3},
                {"kind": "cellTemperature", "max": 400, "x": 2, "y": 3},
            ],
        )
        for assertion in (
            lambda: expect.mass_conserved(-1),
            lambda: expect.total_mass(),
            lambda: expect.total_mass(min=2, max=1),
            lambda: expect.total_mass(max=float("nan")),
        ):
            with self.assertRaises(ValueError):
                assertion()

    def test_duplicate_cases_and_deferred_factories_fail_without_backend_execution(
        self,
    ):
        backend = RecordingHarness()
        suite = Suite(backend)

        @suite.scenario
        @parametrize(mass=[1, 1])
        @expect.mass_conserved()
        def duplicated(mass):
            return Scenario()

        with self.assertRaisesRegex(DefinitionError, "Duplicate scenario name"):
            suite.run()
        self.assertEqual(backend.calls, [])

        async def asynchronous():
            return Scenario()

        suite = Suite(backend)

        @suite.scenario
        def deferred():
            return asynchronous()

        with self.assertRaisesRegex(DefinitionError, "Return a Scenario"):
            suite.run()
        self.assertEqual(backend.calls, [])

    def test_atomic_save_and_empty_report_summary(self):
        report = TestReport({"schemaVersion": 1, "tests": []}, 0, "", "")
        self.assertFalse(report.passed)
        self.assertEqual(str(report), "FAIL: 0/0 tests passed")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            output.write_text("old report", encoding="utf-8")
            self.assertEqual(report.save(output), output)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), report.data
            )


if __name__ == "__main__":
    unittest.main()
