# SPDX-License-Identifier: GPL-3.0-only
param([string]$FixtureParent = $env:TEMP)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'Windows DPAPI tests required' }
$file = Join-Path $PSScriptRoot 'release-signer.ps1'
$CryptoHelper = Join-Path $PSScriptRoot 'release-signer-crypto.js'
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($file, [ref]$null, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Signer parse failed' }
# Load only reviewed function definitions. Do not run Initialize or touch the
# real key namespace; generated fixture keys exist in process memory only.
foreach ($definition in $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    Invoke-Expression $definition.Extent.Text
}
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$SystemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$Entropy = [Text.Encoding]::ASCII.GetBytes('DroidDeck release signing v1')
$fixture = Join-Path ([IO.Path]::GetFullPath($FixtureParent)) ('droiddeck-signer-test-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$passed = 0
try {
    Protect-Path $fixture $true; Assert-Private $fixture; $passed++
    $path = Join-Path $fixture 'public-fixture.json'
    Write-New $path ([Text.Encoding]::ASCII.GetBytes('public fixture'))
    Assert-Private $path; $passed++
    $before = Get-Item -LiteralPath $path
    $refused = $false
    try { Write-New $path ([Text.Encoding]::ASCII.GetBytes('replacement')) } catch { $refused = $true }
    if (-not $refused -or [IO.File]::ReadAllText($path) -cne 'public fixture' -or
        (Get-Item -LiteralPath $path).LastWriteTimeUtc -ne $before.LastWriteTimeUtc) { throw 'CreateNew refusal failed' }
    $passed++
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($Sid); $acl.SetAccessRuleProtection($true, $false)
    foreach ($principal in @($Sid, $SystemSid)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($principal, 'FullControl', 'Allow'))
    }
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new('S-1-5-11'), 'Read', 'Allow'))
    $refused = $false
    try { Assert-Access $acl } catch { $refused = $true }
    if (-not $refused) { throw 'Broadened ACL accepted' }; $passed++
    $generated = Crypto 'generate'
    if ($generated.selfTest -ne $true -or $generated.publicKeySpkiHex.Length -ne 88 -or
        $generated.keySha256.Length -ne 64) { throw 'Fixture signing self-test failed' }; $passed++
    $private = [Convert]::FromBase64String($generated.private)
    try {
        $encrypted = [Security.Cryptography.ProtectedData]::Protect($private, $Entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser)
        if ([Convert]::ToBase64String($encrypted) -ceq $generated.private) { throw 'Plaintext custody' }
        $recovered = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $Entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser)
        try {
            $verified = Crypto 'verify' $recovered
            if ((Public-Record $verified) -cne (Public-Record $generated)) { throw 'DPAPI key identity changed' }
        } finally { [Security.Cryptography.CryptographicOperations]::ZeroMemory($recovered) }
        $passed++
        $refused = $false
        try { $null = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, [byte[]]@(1),
            [Security.Cryptography.DataProtectionScope]::CurrentUser) } catch { $refused = $true }
        if (-not $refused) { throw 'Wrong DPAPI context accepted' }; $passed++
        $encrypted[0] = $encrypted[0] -bxor 1
        $refused = $false
        try { $null = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $Entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser) } catch { $refused = $true }
        if (-not $refused) { throw 'Corrupt custody accepted' }; $passed++
        $backed = Crypto 'backup' $private @{passphrase='fixture passphrase only, not a real password'}
        if (-not $backed.backupRecoveryVerified -or $backed.keySha256 -cne $generated.keySha256) { throw 'Backup bridge failed' }
        $passed++
        $recovered = Crypto 'verifyBackup' @() @{backup=$backed.backup;passphrase='fixture passphrase only, not a real password';
            expectedKeySha256=$generated.keySha256}
        if (-not $recovered.backupRecoveryVerified -or $recovered.keySha256 -cne $generated.keySha256) { throw 'Independent backup bridge failed' }
        $passed++
        $value = [ordered]@{base=[ordered]@{sourceSha256=('a'*64);version='0.1.0-rc.2'};bundleSha256=('b'*64);
            channel='pilot';dataPolicy='preserve';keySha256=$generated.keySha256;kind='droiddeck-runtime-release';
            manifestSha256=('c'*64);platform=[ordered]@{architecture='x86_64';distribution='ubuntu';version='26.04'};
            schema=1;target=[ordered]@{sourceRevision=('d'*40);sourceSha256=('e'*64);version='0.1.0-rc.3'}}
        $statement = [Text.Encoding]::ASCII.GetBytes(($value | ConvertTo-Json -Compress -Depth 8) + "`n")
        $statementHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($statement)).ToLowerInvariant()
        $signed = Crypto 'sign' $private @{statement=[Convert]::ToBase64String($statement);expectedStatementSha256=$statementHash}
        if (-not $signed.releaseSigned -or [Convert]::FromBase64String($signed.signature).Length -ne 64 -or
            $signed.keySha256 -cne $generated.keySha256) { throw 'Signature bridge failed' }
        $passed++
    } finally { [Security.Cryptography.CryptographicOperations]::ZeroMemory($private); $generated.private = $null }
    [ordered]@{kind='windows-signer-tests';passed=$passed;fixturePrivateFilesWritten=0;realSignerChanged=$false} |
        ConvertTo-Json -Compress
} catch {
    Write-Host ('Signer fixture failed after ' + $passed + ' checks: ' + $_.Exception.GetType().Name)
    throw
} finally {
    $resolved = [IO.Path]::GetFullPath($fixture)
    $parent = [IO.Path]::GetFullPath($FixtureParent).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -notmatch '^droiddeck-signer-test-[a-f0-9]{32}$') {
        throw 'Unsafe fixture cleanup refused'
    }
    foreach ($child in Get-ChildItem -LiteralPath $resolved -Force) {
        Remove-Item -LiteralPath $child.FullName -Force
    }
    Remove-Item -LiteralPath $resolved -Force
}
