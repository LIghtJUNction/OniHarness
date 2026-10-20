"""Explicit, lazy scenario collection. Decorating never builds or runs a backend."""

from __future__ import annotations

import copy
import inspect
import itertools
import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, ParamSpec, TypeVar, overload

from ._version import __version__
from .harness import Harness, HarnessError, TestReport
from .scenario import _EXPECTATIONS, Scenario

P = ParamSpec("P")
T = TypeVar("T")
_PARAMETERS = "__oniharness_parameters__"
Factory = Callable[..., Scenario | Mapping[str, Any]]
F = TypeVar("F", bound=Factory)


class DefinitionError(HarnessError):
    """A named scenario definition is invalid; no backend has run."""


def _require_sync(function: Callable[..., Any]) -> None:
    if (
        inspect.iscoroutinefunction(function)
        or inspect.isgeneratorfunction(function)
        or inspect.isasyncgenfunction(function)
    ):
        raise TypeError(
            "Scenario factories must be synchronous functions returning Scenario or a mapping"
        )


def parametrize(
    **parameters: Iterable[Any],
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Create one case per value combination, e.g. ``@parametrize(mass=[1, 2])``.

    Multiple names and stacked decorators form a Cartesian product. The original
    function and signature are retained; factories are only called by Suite.run.
    """
    if not parameters:
        raise ValueError("parametrize requires at least one named parameter")
    axes = []
    for name, values in parameters.items():
        if not name.isidentifier() or isinstance(values, (str, bytes)):
            raise ValueError(
                "Use named parameters with nonempty value iterables, not strings"
            )
        values = tuple(values)
        if not values:
            raise ValueError(
                f"Parameter {name!r} has no values; empty runs are failures"
            )
        axes.append((name, values))

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        _require_sync(function)
        previous = getattr(function, _PARAMETERS, ())
        duplicates = {name for name, _ in previous} & parameters.keys()
        if duplicates:
            raise ValueError("Duplicate parameters: " + ", ".join(sorted(duplicates)))
        setattr(function, _PARAMETERS, (*previous, *axes))
        return function

    return decorate


@dataclass(frozen=True)
class _Registration:
    factory: Factory
    name: str | None
    repeat: int
    timeout: int


class Suite:
    """One configuration, decorated factories and one explicit ``run()``.

    Provide a Harness, a trusted checkout (optionally build=True), or use the
    installed CLI on PATH. Backend creation is deferred until all selected
    definitions have been checked. Each native repetition remains a new worker.
    """

    def __init__(
        self,
        harness: Harness | None = None,
        *,
        checkout: str | os.PathLike[str] | None = None,
        build: bool = False,
        game_path: str | os.PathLike[str] | None = None,
        process_timeout: float = 300,
    ):
        if harness is not None and (
            checkout is not None
            or build
            or game_path is not None
            or process_timeout != 300
        ):
            raise ValueError(
                "Configure either a Harness or checkout/game options, not both"
            )
        if build and checkout is None:
            raise ValueError("build=True requires a trusted checkout path")
        self._harness = harness
        self._checkout = checkout
        self._build = build
        self._game_path = game_path
        self._process_timeout = process_timeout
        self._registrations: list[_Registration] = []

    @overload
    def scenario(self, function: F, /) -> F: ...

    @overload
    def scenario(
        self,
        function: None = None,
        /,
        *,
        name: str | None = None,
        repeat: int = 1,
        timeout: int = 20,
    ) -> Callable[[F], F]: ...

    def scenario(
        self,
        function: Factory | None = None,
        /,
        *,
        name: str | None = None,
        repeat: int = 1,
        timeout: int = 20,
    ) -> Any:
        """Register with ``@suite.scenario`` or ``@suite.scenario(repeat=3)``."""
        if type(repeat) is not int or not 1 <= repeat <= 100:
            raise ValueError("repeat must be an integer in 1..100")
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("timeout must be an integer in 1..600 worker seconds")
        if name is not None and not name.strip():
            raise ValueError("Scenario name cannot be empty")

        def register(factory: Factory) -> Factory:
            _require_sync(factory)
            if any(item.factory is factory for item in self._registrations):
                raise ValueError(f"Scenario {factory.__name__!r} is already registered")
            self._registrations.append(_Registration(factory, name, repeat, timeout))
            return factory

        return register(function) if function is not None else register

    def _prepare(
        self, filter: str | None
    ) -> list[tuple[_Registration, dict[str, Any]]]:
        prepared: list[tuple[_Registration, dict[str, Any]]] = []
        names: set[str] = set()
        for registration in self._registrations:
            factory = registration.factory
            axes = getattr(factory, _PARAMETERS, ())
            for values in itertools.product(*(values for _, values in axes)):
                arguments = dict(zip((name for name, _ in axes), values))
                base_name = registration.name or factory.__name__
                suffix = ", ".join(
                    f"{key}={value!r}" for key, value in arguments.items()
                )
                case_name = base_name + (f"[{suffix}]" if arguments else "")
                if filter is not None and filter.casefold() not in case_name.casefold():
                    continue
                if case_name in names:
                    raise DefinitionError(f"Duplicate scenario name: {case_name}")
                names.add(case_name)
                try:
                    inspect.signature(factory).bind(**arguments)
                    value = factory(**arguments)
                    if inspect.iscoroutine(value):
                        value.close()
                    if isinstance(value, Scenario):
                        scenario = value.to_dict()
                    elif isinstance(value, Mapping):
                        scenario = copy.deepcopy(dict(value))
                    else:
                        raise TypeError(
                            "Return a Scenario or mapping from the scenario factory"
                        )
                    scenario["name"] = case_name
                    assertions = scenario.get("assertions", [])
                    if not isinstance(assertions, (list, tuple)):
                        raise TypeError("assertions must be a sequence")
                    scenario["assertions"] = [
                        *assertions,
                        *(
                            item.to_dict()
                            for item in getattr(factory, _EXPECTATIONS, ())
                        ),
                    ]
                    if not scenario["assertions"]:
                        raise ValueError(
                            "Add @expect.mass_conserved(), another @expect assertion, or Scenario.expect(...)"
                        )
                    # Catch unserializable inputs before building or running any native case.
                    json.dumps(scenario, allow_nan=False)
                except Exception as error:
                    raise DefinitionError(
                        f"{case_name}: {type(error).__name__}: {error}"
                    ) from error
                prepared.append((registration, scenario))
        if not prepared:
            raise DefinitionError(
                "No matching scenarios. Empty test runs are failures."
            )
        return prepared

    def _backend(self) -> Harness:
        if self._harness is None:
            if self._checkout is not None:
                self._harness = Harness.from_checkout(
                    self._checkout,
                    build=self._build,
                    game_path=self._game_path,
                    process_timeout=self._process_timeout,
                )
            else:
                self._harness = Harness(
                    game_path=self._game_path,
                    process_timeout=self._process_timeout,
                )
        return self._harness

    def run(
        self,
        *,
        filter: str | None = None,
        check: bool = True,
        report_path: str | os.PathLike[str] | None = None,
    ) -> TestReport:
        """Run selected cases and retain every worker result, including failures.

        Definition errors occur before any backend execution. CLI setup errors
        retain CommandError behavior. Test failures are collected, saved when
        requested, then raise TestFailure unless check=False.
        """
        prepared = self._prepare(filter)
        started = datetime.now(timezone.utc).isoformat()
        backend = self._backend()
        reports = [
            backend.run_scenario(
                scenario,
                repeat=registration.repeat,
                timeout=registration.timeout,
                check=False,
            )
            for registration, scenario in prepared
        ]
        data = {
            **reports[0].data,
            "startedUtc": started,
            "pythonVersion": __version__,
            "tests": [test for report in reports for test in report.tests],
        }
        report = TestReport(
            data,
            0 if all(item.passed for item in reports) else 1,
            "\n".join(item.stdout for item in reports),
            "\n".join(item.stderr for item in reports),
        )
        if report_path is not None:
            report.save(report_path)
        if check:
            report.raise_for_status()
        return report
