
import pytest

from backend.collectors.base import BaseCollector


def test_base_collector_is_abstract():
    with pytest.raises(TypeError):
        BaseCollector()


def test_concrete_collector_can_be_instantiated():
    class FakeCollector(BaseCollector):
        def collect(self):
            return {"status": "ok"}

    collector = FakeCollector()

    assert collector.collect() == {"status": "ok"}


def test_subclass_must_implement_collect():
    class InvalidCollector(BaseCollector):
        pass

    with pytest.raises(TypeError):
        InvalidCollector()
