param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RepoRoot\src;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "$RepoRoot\src"
}

python -m headless_pr_workflow.cli @Args
