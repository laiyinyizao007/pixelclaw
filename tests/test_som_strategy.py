"""Tests for strategies/som_strategy.py"""
import asyncio
import copy
import pytest
from unittest.mock import AsyncMock, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from strategies.base import Action, ActionType, StrategyLevel, StrategyResult
from strategies.som_strategy import SOMStrategy


def make_result(params: dict, success: bool = True) -> StrategyResult:
    action = Action(
        action_type=ActionType.TAP,
        params=params,
        reasoning="test",
        confidence=1.0,
    )
    return StrategyResult(
        success=success,
        level=StrategyLevel.STEP1V,
        action=action if success else None,
    )


@pytest.fixture
def base_strategy():
    mock = MagicMock()
    mock.get_level.return_value = StrategyLevel.STEP1V
    mock.is_available.return_value = True
    return mock


@pytest.fixture
def wrapper(base_strategy):
    return SOMStrategy(base_strategy)


class TestPassthrough:
    def test_failed_result_returned_as_is(self, wrapper, base_strategy):
        result = make_result({}, success=False)
        base_strategy.analyze = AsyncMock(return_value=result)
        out = asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", {})
        )
        assert out.success is False

    def test_no_som_id_in_params_passthrough(self, wrapper, base_strategy):
        result = make_result({"x": 100, "y": 200})
        base_strategy.analyze = AsyncMock(return_value=result)
        out = asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", {})
        )
        assert out.action.params == {"x": 100, "y": 200}


class TestSomIdResolution:
    def test_valid_som_id_resolved_to_coords(self, wrapper, base_strategy):
        result = make_result({"som_id": 2})
        base_strategy.analyze = AsyncMock(return_value=result)
        context = {"som_mapping": {1: (10, 20), 2: (50, 60), 3: (90, 100)}}
        out = asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", context)
        )
        assert out.action.params["x"] == 50
        assert out.action.params["y"] == 60
        assert "som_id" not in out.action.params

    def test_string_som_id_resolved(self, wrapper, base_strategy):
        result = make_result({"som_id": "3"})
        base_strategy.analyze = AsyncMock(return_value=result)
        context = {"som_mapping": {3: (70, 80)}}
        out = asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", context)
        )
        assert out.action.params["x"] == 70
        assert out.action.params["y"] == 80


class TestMissingId:
    def test_som_id_not_in_mapping_keeps_original(self, wrapper, base_strategy):
        result = make_result({"som_id": 99})
        base_strategy.analyze = AsyncMock(return_value=result)
        context = {"som_mapping": {1: (10, 20)}}
        out = asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", context)
        )
        # som_id retained, no coords injected
        assert "som_id" in out.action.params
        assert "x" not in out.action.params

    def test_empty_som_mapping_keeps_original(self, wrapper, base_strategy):
        result = make_result({"som_id": 1})
        base_strategy.analyze = AsyncMock(return_value=result)
        out = asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", {"som_mapping": {}})
        )
        assert "som_id" in out.action.params

    def test_non_integer_som_id_keeps_original(self, wrapper, base_strategy):
        result = make_result({"som_id": "abc"})
        base_strategy.analyze = AsyncMock(return_value=result)
        out = asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", {"som_mapping": {1: (10, 20)}})
        )
        assert "som_id" in out.action.params


class TestParamsMutationIsolation:
    def test_original_params_not_mutated(self, wrapper, base_strategy):
        original_params = {"som_id": 1, "extra": "value"}
        result = make_result(copy.deepcopy(original_params))
        base_strategy.analyze = AsyncMock(return_value=result)
        context = {"som_mapping": {1: (10, 20)}}
        asyncio.get_event_loop().run_until_complete(
            wrapper.analyze(MagicMock(), "goal", context)
        )
        # The result's action params may be replaced but the input dict is separate
        assert original_params == {"som_id": 1, "extra": "value"}
