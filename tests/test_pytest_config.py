# Description: The suite's own pytest configuration: a passing run keeps no tmp_path directory,
# Description: since kept basetemps once filled a machine's /tmp inodes.

from __future__ import annotations

import pytest


def test_tmp_path_retention_keeps_failed_tests_only(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.getini("tmp_path_retention_policy") == "failed"
