# SPDX-License-Identifier: GPL-3.0-only
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Initialize', 'Inspect', 'ExportTrust', 'Sign', 'Backup', 'VerifyBackup')][string]$Action,
    [string]$OutputPath,
    [string]$StatementPath,
    [string]$BundlePath,
    [string]$BackupPath,
    [string]$ExpectedPublicKeySha256
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'Windows current-user signing custody required' }
$Parent = Join-Path $HOME '.droiddeck-signing'
$Root = Join-Path $Parent 'pilot'
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$SystemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$Entropy = [Text.Encoding]::ASCII.GetBytes('DroidDeck release signing v1')
$Names = @('private.dpapi', 'public.der', 'complete.json')
$CryptoHelper = Join-Path $PSScriptRoot 'release-signer-crypto.js'

function Assert-Private([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked signer path refused' }
    $acl = Get-Acl -LiteralPath $Path
    Assert-Access $acl
    if (-not $item.PSIsContainer -and ($item.Length -lt 1 -or $item.Length -gt 4096)) {
        throw 'Bounded signer file required'
    }
}

function Assert-Access($acl) {
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $Sid.Value -or
        -not $acl.AreAccessRulesProtected) { throw 'Explicit current-user protected ACL required' }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.IsInherited -or $rule.AccessControlType -ne 'Allow' -or
            $rule.IdentityReference.Value -notin @($Sid.Value, $SystemSid.Value)) {
            throw 'Unexpected signing-custody access rule'
        }
    }
    foreach ($principal in @($Sid, $SystemSid)) {
        $rules = @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]) |
            Where-Object { $_.IdentityReference.Value -eq $principal.Value -and $_.FileSystemRights -eq 'FullControl' })
        if ($rules.Count -ne 1) { throw 'Exact signer access rules required' }
    }
}

function Protect-Path([string]$Path, [bool]$Directory) {
    $acl = if ($Directory) { [Security.AccessControl.DirectorySecurity]::new() }
           else { [Security.AccessControl.FileSecurity]::new() }
    $acl.SetOwner($Sid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($principal in @($Sid, $SystemSid)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($principal, 'FullControl', 'Allow'))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    Assert-Private $Path
}

function Write-New([string]$Path, [byte[]]$Bytes) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Bytes); $stream.Flush($true) } finally { $stream.Dispose() }
    Protect-Path $Path $false
}

function Invoke-Bridge([string]$Binary, [string[]]$Arguments, [hashtable]$Request) {
    # All private responses stay captured in memory. Never print this protocol.
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Binary
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = 'Hidden'
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $start
    try {
        $null = $process.Start()
        $process.StandardInput.Write(($Request | ConvertTo-Json -Compress -Depth 8))
        $process.StandardInput.Close()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) { $process.Kill($true); throw 'Signing key subprocess timed out' }
        $result = $outputTask.GetAwaiter().GetResult()
        $errorText = $errorTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0 -or $errorText -or $result.Length -gt 4096) { throw 'Signing key self-test failed' }
        return $result | ConvertFrom-Json
    } finally { $process.Dispose() }
}

function Crypto([string]$Mode, [byte[]]$Private = @(), [hashtable]$Extra = @{}) {
    $request = @{mode=$Mode;private=[Convert]::ToBase64String($Private)}
    foreach ($key in $Extra.Keys) { $request[$key] = $Extra[$key] }
    return Invoke-Bridge (Join-Path $env:ProgramFiles 'nodejs/node.exe') @($CryptoHelper) $request
}

function Read-Bounded([string]$Path, [long]$Maximum) {
    if (-not $Path -or -not [IO.Path]::IsPathFullyQualified($Path)) { throw 'Absolute bounded input required' }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint -or
        $item.Length -lt 1 -or $item.Length -gt $Maximum) { throw 'Bounded regular input required' }
    $raw = [IO.File]::ReadAllBytes($item.FullName)
    if ($raw.Length -ne $item.Length) { throw 'Input length changed' }
    return ,$raw
}

function Read-Passphrase([bool]$Confirm) {
    $secure = Read-Host 'Backup passphrase (20+ characters, never enter it in chat)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $secure.Dispose() }
    if ($value.Length -lt 20 -or $value.Length -gt 1024) { throw 'Passphrase must be 20 to 1024 characters' }
    if ($Confirm) {
        $again = Read-Passphrase $false
        if ($value -cne $again) { throw 'Backup passphrases differ' }
    }
    return $value
}

function Public-Record($Value) {
    return '{"algorithm":"Ed25519","custody":"windows-dpapi-current-user","keySha256":"' +
        $Value.keySha256 + '","publicKeySpkiHex":"' + $Value.publicKeySpkiHex + '","schema":1}' + "`n"
}

if ($Action -notin @('ExportTrust', 'Sign', 'Backup') -and $OutputPath) { throw 'No output file accepted for this operation' }
if ($Action -ne 'Sign' -and ($StatementPath -or $BundlePath)) { throw 'Release inputs require Sign' }
if ($Action -ne 'VerifyBackup' -and ($BackupPath -or $ExpectedPublicKeySha256)) { throw 'Backup recovery inputs require VerifyBackup' }
if ($Action -eq 'VerifyBackup') {
    if ($ExpectedPublicKeySha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'Separately recorded public fingerprint required' }
    $backup = [Text.Encoding]::UTF8.GetString((Read-Bounded $BackupPath 4096)) | ConvertFrom-Json -AsHashtable
    $passphrase = Read-Passphrase $false
    try { $verified = Crypto 'verifyBackup' @() @{backup=$backup;passphrase=$passphrase;expectedKeySha256=$ExpectedPublicKeySha256} }
    finally { $passphrase = $null }
    [ordered]@{kind='droiddeck-portable-backup-verification';keySha256=$verified.keySha256;
        backupRecoveryVerified=$verified.backupRecoveryVerified;dpapiUsed=$false;privateKeyRestoredToDisk=$false} | ConvertTo-Json -Compress
    return
}
if ($Action -eq 'Initialize' -and -not (Test-Path -LiteralPath $Parent)) {
    $null = New-Item -ItemType Directory -Path $Parent -ErrorAction Stop
}
$parentItem = Get-Item -LiteralPath $Parent -Force
if (-not $parentItem.PSIsContainer -or $parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Original signer parent directory required'
}
if ($Action -eq 'Initialize') {
    if (Test-Path -LiteralPath $Root) { throw 'Signer namespace already exists; never replace or adopt it' }
    $null = New-Item -ItemType Directory -Path $Root -ErrorAction Stop
    Protect-Path $Root $true
    $generated = Crypto 'generate'
    $private = [Convert]::FromBase64String($generated.private)
    try {
        $encrypted = [Security.Cryptography.ProtectedData]::Protect($private, $Entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser)
        Write-New (Join-Path $Root 'private.dpapi') $encrypted
    } finally { [Security.Cryptography.CryptographicOperations]::ZeroMemory($private); $generated.private = $null }
    Write-New (Join-Path $Root 'public.der') ([Convert]::FromHexString($generated.publicKeySpkiHex))
    Write-New (Join-Path $Root 'complete.json') ([Text.Encoding]::ASCII.GetBytes((Public-Record $generated)))
}

Assert-Private $Root
$actualNames = @(Get-ChildItem -LiteralPath $Root -Force | Select-Object -ExpandProperty Name | Sort-Object) -join ','
$expectedNames = ($Names | Sort-Object) -join ','
if ($actualNames -cne $expectedNames) { throw 'Incomplete or unexpected signing namespace; preserve it' }
foreach ($name in $Names) { Assert-Private (Join-Path $Root $name) }
$public = [IO.File]::ReadAllBytes((Join-Path $Root 'public.der'))
$identity = [pscustomobject]@{keySha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($public)).ToLowerInvariant();
    publicKeySpkiHex=[Convert]::ToHexString($public).ToLowerInvariant()}
if ([IO.File]::ReadAllText((Join-Path $Root 'complete.json')) -cne (Public-Record $identity)) { throw 'Original public identity required' }
$statement = $null
if ($Action -eq 'Sign') {
    $statement = Read-Bounded $StatementPath 8192
    $bundle = Read-Bounded $BundlePath (80 * 1024 * 1024)
    $policy = Join-Path $PSScriptRoot '../runtime/portable/release-sign-inputs.py'
    $python = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
    $review = Invoke-Bridge $python @('-I', '-B', $policy) @{
        bundle=[Convert]::ToBase64String($bundle);statement=[Convert]::ToBase64String($statement);keySha256=$identity.keySha256}
    if ($review.kind -cne 'droiddeck-release-signing-inputs' -or $review.keySha256 -cne $identity.keySha256) { throw 'Reviewed signing inputs required' }
}
$private = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $Root 'private.dpapi')),
    $Entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
try { $verified = Crypto 'verify' $private }
finally { [Security.Cryptography.CryptographicOperations]::ZeroMemory($private) }
if (-not $verified.selfTest -or [Convert]::ToHexString([IO.File]::ReadAllBytes((Join-Path $Root 'public.der'))).ToLowerInvariant() -ne
    $verified.publicKeySpkiHex -or [IO.File]::ReadAllText((Join-Path $Root 'complete.json')) -cne (Public-Record $verified)) {
    throw 'Original signing key/public record mismatch'
}
if ($Action -eq 'Sign' -or $Action -eq 'Backup') {
    if (-not $OutputPath -or -not [IO.Path]::IsPathFullyQualified($OutputPath)) { throw 'New absolute protected output required' }
    $output = [IO.Path]::GetFullPath($OutputPath)
    if ($output.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'No writes into original custody' }
    $passphrase = $null
    if ($Action -eq 'Backup') {
        $backupRoot = Join-Path $Parent 'portable-backup'
        if ([IO.Path]::GetDirectoryName($output) -cne $backupRoot) { throw 'Encrypted backup must stay in dedicated outside-Git backup custody' }
        if (-not (Test-Path -LiteralPath $backupRoot)) { $null = New-Item -ItemType Directory -Path $backupRoot; Protect-Path $backupRoot $true }
        Assert-Private $backupRoot
        if (Test-Path -LiteralPath $output) { throw 'Existing backup must not be overwritten' }
        $passphrase = Read-Passphrase $true
    }
    $private = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $Root 'private.dpapi')),
        $Entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    try {
        if ($Action -eq 'Sign') {
            $signed = Crypto 'sign' $private @{statement=[Convert]::ToBase64String($statement);expectedStatementSha256=$review.statementSha256}
            Write-New $output ([Convert]::FromBase64String($signed.signature))
        } else {
            $backed = Crypto 'backup' $private @{passphrase=$passphrase}
            Write-New $output ([Text.Encoding]::ASCII.GetBytes(($backed.backup | ConvertTo-Json -Compress -Depth 8) + "`n"))
        }
    } finally { [Security.Cryptography.CryptographicOperations]::ZeroMemory($private); $passphrase = $null }
}
if ($Action -eq 'ExportTrust') {
    if (-not $OutputPath -or -not [IO.Path]::IsPathFullyQualified($OutputPath)) { throw 'Absolute new public trust output required' }
    $output = [IO.Path]::GetFullPath($OutputPath)
    if ($output.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Public export must be outside the private signing namespace'
    }
    $trust = '{"channel":"pilot","kind":"droiddeck-update-trust","publicKeySpkiHex":"' +
        $verified.publicKeySpkiHex + '","schema":1}' + "`n"
    Write-New $output ([Text.Encoding]::ASCII.GetBytes($trust))
}
[ordered]@{kind='droiddeck-signing-custody';algorithm='Ed25519';keySha256=$verified.keySha256;
    publicKeySpkiHex=$verified.publicKeySpkiHex;windowsCurrentUserProtectionVerified=$true;
    signingSelfTestPassed=$true;plaintextPrivateKeyExported=$false;releaseSigned=($Action -eq 'Sign');
    portableEncryptedBackupCreated=($Action -eq 'Backup');offlineBackupCreated=$false} | ConvertTo-Json -Compress
