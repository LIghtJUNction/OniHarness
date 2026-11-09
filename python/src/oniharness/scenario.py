"""Small immutable scenario values, with assertions usable as decorators."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
_EXPECTATIONS = "__oniharness_expectations__"


@dataclass(frozen=True)
class Cell:
    """Fixture element, kilograms and Kelvin; defaults to 1 kg oxygen at 300 K."""

    element: str = "Oxygen"
    mass: float = 1
    temperature: float = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "element": self.element,
            "mass": self.mass,
            "temperature": self.temperature,
        }


@dataclass(frozen=True)
class Region:
    """A rectangle applied after the fixture's sealed boundary."""

    x: int
    y: int
    width: int = 1
    height: int = 1
    cell: Cell = field(default_factory=Cell)

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "cell": self.cell.to_dict(),
        }


@dataclass(frozen=True)
class Point2D:
    x: int
    y: int

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class PointsRegion:
    points: tuple[Point2D, ...]
    cell: Cell = field(default_factory=Cell)

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": [p.to_dict() for p in self.points],
            "cell": self.cell.to_dict(),
        }


@dataclass(frozen=True)
class PolygonRegion:
    vertices: tuple[Point2D, ...]
    cell: Cell = field(default_factory=Cell)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vertices": [v.to_dict() for v in self.vertices],
            "cell": self.cell.to_dict(),
        }


@dataclass(frozen=True)
class BuildingPlacement:
    building_def: str
    x: int
    y: int
    element: str = "Granite"
    temperature: float = 300
    mass: float = 200
    is_open: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "buildingDef": self.building_def,
            "x": self.x,
            "y": self.y,
            "element": self.element,
            "temperature": self.temperature,
            "mass": self.mass,
            "isOpen": self.is_open,
        }


@dataclass(frozen=True)
class Expectation:
    """One native assertion. Also decorates a scenario factory without wrapping it."""

    kind: str
    min: float | None = None
    max: float | None = None
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    width: int | None = None
    height: int | None = None
    absolute_tolerance: float | None = None

    def __post_init__(self) -> None:
        if self.kind == "massConserved":
            tolerance = self.absolute_tolerance
            if tolerance is None or not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("Mass tolerance must be finite and nonnegative")
        elif self.kind in ("communicatingVesselsEquilibrium", "columnHeightNear"):
            if self.x is None or self.x2 is None:
                raise ValueError("Communicating vessels equilibrium requires x and x2")
        else:
            if self.min is None and self.max is None:
                raise ValueError("Provide min and/or max for a range expectation")
            if any(
                value is not None and not math.isfinite(value)
                for value in (self.min, self.max)
            ) or (
                self.min is not None and self.max is not None and self.min > self.max
            ):
                raise ValueError("Expectation range must be finite and ordered")

    def to_dict(self) -> dict[str, Any]:
        values = {
            "kind": self.kind,
            "min": self.min,
            "max": self.max,
            "x": self.x,
            "y": self.y,
            "x2": self.x2,
            "y2": self.y2,
            "width": self.width,
            "height": self.height,
            "absoluteTolerance": self.absolute_tolerance,
        }
        return {key: value for key, value in values.items() if value is not None}

    def __call__(self, function: Callable[P, T]) -> Callable[P, T]:
        previous = getattr(function, _EXPECTATIONS, ())
        setattr(function, _EXPECTATIONS, (*previous, self))
        return function


class _Expect:
    """Assertions shared by decorators and ``Scenario.expect(...)``."""

    @staticmethod
    def mass_conserved(tolerance: float = 0.01) -> Expectation:
        return Expectation("massConserved", absolute_tolerance=tolerance)

    @staticmethod
    def total_mass(
        *, min: float | None = None, max: float | None = None
    ) -> Expectation:
        return Expectation("totalMass", min=min, max=max)

    @staticmethod
    def cell_mass(
        x: int, y: int, *, min: float | None = None, max: float | None = None
    ) -> Expectation:
        return Expectation("cellMass", min=min, max=max, x=x, y=y)

    @staticmethod
    def cell_temperature(
        x: int, y: int, *, min: float | None = None, max: float | None = None
    ) -> Expectation:
        return Expectation("cellTemperature", min=min, max=max, x=x, y=y)

    @staticmethod
    def region_mass(
        x: int, y: int, width: int = 1, height: int = 1, *, min: float | None = None, max: float | None = None
    ) -> Expectation:
        return Expectation("regionMass", min=min, max=max, x=x, y=y, width=width, height=height)

    @staticmethod
    def column_liquid_level(
        x: int, *, min: float | None = None, max: float | None = None
    ) -> Expectation:
        return Expectation("columnLiquidLevel", min=min, max=max, x=x, y=0)

    @staticmethod
    def communicating_vessels_equilibrium(
        x1: int, x2: int, tolerance: float = 1.0
    ) -> Expectation:
        return Expectation("communicatingVesselsEquilibrium", x=x1, x2=x2, absolute_tolerance=tolerance)


expect = _Expect()


@dataclass(frozen=True)
class Scenario:
    """Typed inputs for the native fixture, not a replacement game simulator.

    Physical/schema checks remain in the official backend. ``expect`` returns a
    new scenario, so a shared base can safely be reused across parameter cases.
    """

    name: str | None = None
    width: int = 16
    height: int = 16
    steps: int = 50
    seed: int = 42
    seconds_per_step: float = 0.2
    fill: Cell = field(default_factory=Cell)
    boundary: Cell = field(default_factory=lambda: Cell("Unobtanium", mass=20000))
    regions: tuple[Region, ...] = ()
    points_regions: tuple[PointsRegion, ...] = ()
    polygon_regions: tuple[PolygonRegion, ...] = ()
    ascii_map: tuple[str, ...] = ()
    legend: Mapping[str, Cell] | None = None
    ascii_origin_x: int = 0
    ascii_origin_y: int = 0
    buildings: tuple[BuildingPlacement, ...] = ()
    assertions: tuple[Expectation, ...] = ()
    elements: tuple[Mapping[str, Any], ...] | None = None

    def expect(self, *assertions: Expectation) -> Scenario:
        return replace(self, assertions=(*self.assertions, *assertions))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "seed": self.seed,
            "secondsPerStep": self.seconds_per_step,
            "fill": self.fill.to_dict(),
            "boundary": self.boundary.to_dict(),
            "regions": [region.to_dict() for region in self.regions],
            "pointsRegions": [pr.to_dict() for pr in self.points_regions],
            "polygonRegions": [pr.to_dict() for pr in self.polygon_regions],
            "asciiMap": list(self.ascii_map),
            "legend": {k: v.to_dict() for k, v in self.legend.items()} if self.legend else {},
            "asciiOriginX": self.ascii_origin_x,
            "asciiOriginY": self.ascii_origin_y,
            "buildings": [b.to_dict() for b in self.buildings],
            "assertions": [assertion.to_dict() for assertion in self.assertions],
        }
        if self.name is not None:
            result["name"] = self.name
        if self.elements is not None:
            result["elements"] = copy.deepcopy(
                [dict(element) for element in self.elements]
            )
        return result
