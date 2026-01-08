Param(
  [string]$Path = "",
  [int]$Tail = 300,
  [string]$Symbol = ""
)

$ErrorActionPreference = "Stop"

$py = @("python", "-X", "utf8", "tools/decisions_src_report.py")
if ($Path -ne "") { $py += @("--path", $Path) }
$py += @("--tail", "$Tail")
if ($Symbol -ne "") { $py += @("--symbol", $Symbol) }

& $py[0] $py[1..($py.Length-1)]
exit $LASTEXITCODE

