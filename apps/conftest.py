import pytest
from django.utils import translation


@pytest.fixture(autouse=True)
def _english_language():
    translation.activate("en")
    yield
    translation.deactivate()
