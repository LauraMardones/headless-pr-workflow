# Windows-only bash start-up file for the test harnesses (issue #398), set as BASH_ENV by
# tests/script_tools.windows_env(). A native jq.exe ends each output line with CRLF under
# Git Bash, so `$(jq -r ...)` keeps a trailing \r and "500\r" never equals "500".
# `-b` (jq 1.7+) writes LF, as on Linux. A function costs no extra process, unlike a wrapper script.
jq() { command jq -b "$@"; }
