import subprocess
import sys
import unittest

from streetlab.roads.hpc_transport import run_step


class HpcTransportTests(unittest.TestCase):
    def test_child_stdin_is_eof_instead_of_the_interactive_terminal(self):
        code = run_step([sys._base_executable, '-c',
                         'import sys; sys.exit(0 if sys.stdin.read() == "" else 9)'], 5, 'stdin-test')
        self.assertEqual(code, 0)

    def test_nonzero_remote_exit_is_preserved(self):
        self.assertEqual(run_step([sys._base_executable, '-c', 'raise SystemExit(7)'], 5, 'exit-test'), 7)

    def test_stalled_step_is_terminated_with_timeout_exit(self):
        self.assertEqual(run_step([sys._base_executable, '-c', 'import time; time.sleep(30)'],
                                  0.2, 'timeout-test'), 124)


if __name__ == '__main__':
    unittest.main()
