# SPDX-License-Identifier: GPL-3.0-only
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'release-signer.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Signer syntax errors' }
$resolver = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-SigningPolicy' }, $true)
if (-not $resolver) { throw 'Public signing policy resolver is missing' }
# Execute only the isolated public resolver definition, never the custody script.
. ([ScriptBlock]::Create($resolver.Extent.Text))
$expected = @{
    Sign = @('../runtime/portable/release-sign-inputs.py', 'droiddeck-release-signing-inputs', 'sign')
    SignCaptureCandidate = @('../runtime/portable/capture-sign-inputs.py', 'droiddeck-capture-signing-inputs', 'signCaptureCandidate')
    SignCaptureActivation = @('../runtime/portable/capture-activation-sign-inputs.py', 'droiddeck-capture-activation-signing-inputs', 'signCaptureActivation')
    SignAggregate = @('aggregate-sign-inputs.py', 'droidmate-aggregate-signing-inputs', 'signAggregate')
    SignSuccessor = @('rc18-sign-inputs.py', 'droidmate-successor-signing-inputs', 'signSuccessor')
}
foreach ($name in $expected.Keys) {
    $actual = Get-SigningPolicy $name
    if (($actual.Keys | Sort-Object) -join ',' -cne 'cryptoMode,file,reviewKind') { throw 'Unexpected public policy fields' }
    if ($actual.file -cne $expected[$name][0] -or $actual.reviewKind -cne $expected[$name][1] -or $actual.cryptoMode -cne $expected[$name][2]) { throw "Wrong policy for $name" }
}
foreach ($name in @('Initialize', 'Backup', '', 'other', 'Sign;other')) {
    $refused = $false
    try { $null = Get-SigningPolicy $name } catch { $refused = $true }
    if (-not $refused) { throw 'Unknown signing operation was accepted' }
}
# Test the actual public-review block with a refusing bridge and a decryption
# reachability marker. AST ordering must place this block before Unprotect.
$reviewBlock = $ast.Find({ param($node) $node -is [Management.Automation.Language.IfStatementAst] -and
    $node.Extent.Text.Contains('$statement = Read-Bounded $StatementPath') }, $true)
$decrypt = $ast.Find({ param($node) $node -is [Management.Automation.Language.InvokeMemberExpressionAst] -and
    $node.Static -and $node.Member.Extent.Text -eq 'Unprotect' }, $true)
if (-not $reviewBlock -or -not $decrypt -or $reviewBlock.Extent.EndOffset -ge $decrypt.Extent.StartOffset) { throw 'Public review must precede custody decryption' }
function Read-Bounded([string]$Path, [int]$Maximum) { return ,([byte[]]@(1, 2, 3)) }
$script:bridgeCalled = 0; $script:decryptReached = $false
function Invoke-Bridge($Binary, $Arguments, $Request) { $script:bridgeCalled++; throw 'fixture-public-policy-refused' }
$SigningActions = @('SignAggregate', 'SignSuccessor'); $StatementPath = 'fixture'; $BundlePath = 'fixture'
$identity = [pscustomobject]@{keySha256=('a'*64)}
$publicBlock = $reviewBlock.Extent.Text.Replace('$PSScriptRoot', ("'" + $PSScriptRoot.Replace("'", "''") + "'"))
foreach ($Action in $SigningActions) {
    $PredecessorPath = if ($Action -eq 'SignSuccessor') { 'fixture-predecessor' } else { $null }
    $script:bridgeCalled = 0; $script:decryptReached = $false; $refused = $false
    try { . ([ScriptBlock]::Create($publicBlock + "`n" + '$script:decryptReached = $true')) }
    catch { if ($_.Exception.Message -cne 'fixture-public-policy-refused') { throw }; $refused = $true }
    if (-not $refused -or $script:bridgeCalled -ne 1 -or $script:decryptReached) { throw 'Rejected policy reached decryption' }
}
Write-Output 'PASS: five public policies, unknown-mode refusal and both aggregate rejection paths before decryption; no custody accessed'
