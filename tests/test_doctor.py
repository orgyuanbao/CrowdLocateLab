"""Environment checks never authenticate or request model inference."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import doctor


class DoctorTests(unittest.TestCase):
    def test_missing_cli_is_optional_for_numerical_reproduction(self):
        with patch.dict("os.environ", {}, clear=True), patch("doctor.shutil.which", return_value=None), patch("doctor.subprocess.run") as run:
            result = doctor.check_environment()
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["checks"][-1]["status"], "warning")
        self.assertFalse(result["credentials_read"])
        self.assertEqual(result["model_calls"], 0)
        run.assert_not_called()

    def test_explicit_cli_requirement_fails_without_executable(self):
        with patch.dict("os.environ", {}, clear=True), patch("doctor.shutil.which", return_value=None):
            result = doctor.check_environment(require_codex=True)
        self.assertEqual(result["error_count"], 1)

    def test_only_version_command_is_called(self):
        with patch.dict("os.environ", {"CODEX_EXECUTABLE": "fixture-codex"}, clear=True), patch("doctor.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="codex-cli 0.155.0-alpha.16.4\n")) as run:
            result = doctor.check_environment(require_codex=True)
        self.assertEqual(run.call_args.args[0], ["fixture-codex", "--version"])
        self.assertEqual(result["checks"][-1]["version"], "0.155.0-alpha.16.4")
        self.assertEqual(result["checks"][-1]["authentication"], "not_checked")
        self.assertFalse(result["login_attempted"])

    def test_missing_numeric_dependency_is_an_error(self):
        real_import = doctor.importlib.import_module
        def missing_numpy(name):
            if name == "numpy":
                raise ImportError("fixture missing module")
            return real_import(name)
        with patch("doctor.importlib.import_module", side_effect=missing_numpy), patch.dict("os.environ", {}, clear=True), patch("doctor.shutil.which", return_value=None):
            result = doctor.check_environment()
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["checks"][1]["error_type"], "ImportError")


if __name__ == "__main__":
    unittest.main()
