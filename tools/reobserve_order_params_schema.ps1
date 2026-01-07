Param(
  [string]$Path = "",
  [int]$Tail = 5,
  [ValidateSet("all","tail")]
  [string]$Scope = "all"
)

$ErrorActionPreference = "Stop"

$py = @("python", "-X", "utf8", "tools/reobserve_order_params_schema.py")
if ($Path -ne "") { $py += @("--path", $Path) }
$py += @("--tail", "$Tail")
$py += @("--scope", "$Scope")

& $py[0] $py[1..($py.Length-1)]
exit $LASTEXITCODE


