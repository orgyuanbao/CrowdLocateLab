"""Run unchanged upstream tests with explicit Windows-only display fixtures.

Three upstream formatting fixtures assert the Windows label "PowerShell" for
bare commands. Run only those fixtures with a module-local Windows name proxy;
never change global os.name, production code, expected assertions, or vendor
bytes. Two additional checks exercise the native and explicit-PowerShell labels.
No process is launched to perform a real model call.
"""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
WINDOWS_FORMATTING_FIXTURES = frozenset({
    'test_formats_live_progress_events',
    'test_formats_other_event_types_clearly',
    'test_progress_renderer_reports_long_and_interrupted_tools',
})


class PlatformName:
    """Override one module's platform label without changing Python's OS module."""
    def __init__(self, name):
        self.name = name

    def __getattr__(self, key):
        return getattr(os, key)


def load_upstream():
    path = ROOT / 'vendor/tests/test_agent.py'
    spec = importlib.util.spec_from_file_location('unchanged_upstream_test_agent', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows_fixture(method, agent):
    def run():
        with mock.patch.object(agent, 'os', PlatformName('nt')):
            return method()
    return run


def main():
    # The command-construction fixtures need an existing executable, but do not run it.
    os.environ['CODEX_EXECUTABLE'] = sys.executable
    module = load_upstream()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.AgentFrameworkTests)
    adapted = set()
    for case in suite:
        name = case._testMethodName
        if name in WINDOWS_FORMATTING_FIXTURES:
            setattr(case, name, windows_fixture(getattr(case, name), module.AGENT))
            adapted.add(name)
    if adapted != WINDOWS_FORMATTING_FIXTURES or suite.countTestCases() != 22:
        raise RuntimeError('Upstream fixture inventory changed; review the adapter.')

    class PlatformLabelChecks(unittest.TestCase):
        def test_native_shell_label_without_proxy(self):
            self.assertIs(module.AGENT.os, os)
            expected = 'PowerShell' if os.name == 'nt' else 'Shell'
            self.assertEqual(module.AGENT.tool_display_name({
                'type': 'command_execution', 'command': 'python -m unittest'}),
                expected + ' | Run tests')

        def test_explicit_powershell_labels_on_posix(self):
            with mock.patch.object(module.AGENT, 'os', PlatformName('posix')):
                for executable in ('pwsh', 'powershell'):
                    with self.subTest(executable=executable):
                        self.assertEqual(module.AGENT.tool_display_name({
                            'type': 'command_execution',
                            'command': executable + ' -Command "python -m unittest"'}),
                            'PowerShell | Run tests')
            self.assertIs(module.AGENT.os, os)

    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PlatformLabelChecks))
    print('Upstream: 22 unchanged tests; 3 explicit Windows display fixtures. '
          'Additional native/explicit-shell checks: 2. No model calls.', flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
