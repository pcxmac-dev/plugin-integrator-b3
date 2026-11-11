"""Every plugin here conforms to the class contract (plugin API 2.2): liminal_libs.sources.conformance,
read from the plugin alone (nothing is sent). The user, 2026-11-03: "evaluate each functional piece
of software or plugin and verify correctness and valid functionality through unit tests created to
address new class scheme"; 2026-11-11: "each plugin should reflect the the API its attached to"."""
from __future__ import annotations

import plugin_integrator_b3 as module
from liminal_libs.sources.conformance import assert_module_conforms


def test_every_plugin_here_conforms_to_the_class_contract():
    found = assert_module_conforms(module)
    assert found and all(p.api == "2.2" and not p.is_record for p in found)  # classes, written for 2.2
    assert {p.SYSTEM for p in found} == {"integrator"}  # the system it plugs into, by its name
    assert all(p.apis for p in found)  # 2.2: each says what it attaches to (the check above holds the declarations)
