"""Regression tests for parent-case counts in engineering review summaries."""
import io
import unittest

from verification.hardening.run_checks import SuccessfulCaseResult, summarize_tests


class TestQualityCheckCounts(unittest.TestCase):
    def summarize_case(self, case):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
        result = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=SuccessfulCaseResult,
        ).run(suite)
        return summarize_tests(result)

    def test_multiple_failing_subtests_count_as_one_unsuccessful_case(self):
        class Cases(unittest.TestCase):
            def test_failures(self):
                for value in range(3):
                    with self.subTest(value=value):
                        self.fail('intentional failure')

            def test_pass(self):
                self.assertEqual(2 + 2, 4)

        summary = self.summarize_case(Cases)
        self.assertEqual(summary['run'], 2)
        self.assertEqual(summary['passed'], 1)
        self.assertEqual(summary['failures'], 3)
        self.assertEqual(summary['errors'], 0)
        self.assertFalse(summary['successful'])

    def test_multiple_error_subtests_preserve_each_diagnostic(self):
        class Cases(unittest.TestCase):
            def test_errors(self):
                for value in range(4):
                    with self.subTest(value=value):
                        raise ValueError('intentional error')

            def test_pass(self):
                with self.subTest(value=1):
                    self.assertTrue(True)
                with self.subTest(value=2):
                    self.assertTrue(True)

        summary = self.summarize_case(Cases)
        self.assertEqual(summary['run'], 2)
        self.assertEqual(summary['passed'], 1)
        self.assertEqual(summary['failures'], 0)
        self.assertEqual(summary['errors'], 4)
        self.assertFalse(summary['successful'])

    def test_skipped_cases_and_subtests_are_not_passed_cases(self):
        class Cases(unittest.TestCase):
            @unittest.skip('whole case')
            def test_skip(self):
                self.fail('must not run')

            def test_skipped_subtests(self):
                for value in range(3):
                    with self.subTest(value=value):
                        self.skipTest('subtest')
                with self.subTest(value='passed'):
                    self.assertTrue(True)

            def test_pass(self):
                self.assertTrue(True)

        summary = self.summarize_case(Cases)
        self.assertEqual(summary['run'], 3)
        self.assertEqual(summary['passed'], 1)
        self.assertEqual(len(summary['skipped']), 4)
        self.assertCountEqual(
            [item['reason'] for item in summary['skipped']],
            ['whole case', 'subtest', 'subtest', 'subtest'],
        )
        self.assertTrue(summary['successful'])

    def test_expected_failures_and_unexpected_successes_are_not_passes(self):
        class Cases(unittest.TestCase):
            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail('expected failure')

            @unittest.expectedFailure
            def test_unexpected_success(self):
                self.assertTrue(True)

            def test_pass(self):
                self.assertTrue(True)

        summary = self.summarize_case(Cases)
        self.assertEqual(summary['run'], 3)
        self.assertEqual(summary['passed'], 1)
        self.assertFalse(summary['successful'])


if __name__ == '__main__':
    unittest.main()
