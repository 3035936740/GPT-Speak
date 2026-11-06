param(
    [switch]$DryRun, [switch]$Quiet, [switch]$WebUI, [switch]$CLI,
    [switch]$NoCooldown, [switch]$WithCooldown,
    [string]$Model, [string]$ReasoningEffort,
    [ValidateSet('', 'on', 'off')][string]$FastMode = '',
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$ExtraArgs
)
$entryArgs = @()
if ($DryRun) { $entryArgs += '--dry-run' }
if ($Quiet) { $entryArgs += '--quiet' }
if ($CLI) { $entryArgs += '--cli' }
if ($PSBoundParameters.ContainsKey('NoCooldown')) { $entryArgs += @('--cooldown', $(if ($NoCooldown) { 'off' } else { 'on' })) }
if ($WithCooldown) { $entryArgs += @('--cooldown', 'on') }
if ($Model) { $entryArgs += @('--model', $Model) }
if ($ReasoningEffort) { $entryArgs += @('--effort', $ReasoningEffort) }
if ($FastMode) { $entryArgs += @('--fast', $FastMode) }
$entryArgs += $ExtraArgs
& (Join-Path $PSScriptRoot 'scripts/run.ps1') -Entry 'bot' @entryArgs
