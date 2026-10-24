# SPDX-License-Identifier: GPL-3.0-only
param([Parameter(Mandatory)][string]$FixtureParent)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'Windows integration fixtures required' }
$fixture = Join-Path ([IO.Path]::GetFullPath($FixtureParent)) ('droiddeck-signer-integration-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$source = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'release-signer.ps1'))
$crypto = Join-Path $PSScriptRoot 'release-signer-crypto.js'
$policy = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../runtime/portable/release-sign-inputs.py'))
$portable = [IO.Path]::GetDirectoryName($policy)
$parent = Join-Path $fixture 'custody'
$replacements = @{
    '$Parent = Join-Path $HOME ''.droiddeck-signing''' = ('$Parent = ''' + $parent.Replace("'", "''") + "'")
    '$CryptoHelper = Join-Path $PSScriptRoot ''release-signer-crypto.js''' = ('$CryptoHelper = ''' + $crypto.Replace("'", "''") + "'")
    '$policy = Join-Path $PSScriptRoot ''../runtime/portable/release-sign-inputs.py''' = ('$policy = ''' + $policy.Replace("'", "''") + "'")
}
foreach ($before in $replacements.Keys) {
    if (($source.Split($before, [StringSplitOptions]::None).Count - 1) -ne 1) { throw 'Exact trusted fixture redirect required' }
    $source = $source.Replace($before, $replacements[$before])
}
$signer = [scriptblock]::Create($source)
$passed = 0
# Only this test harness supplies a fixture password. Real CLI uses a secure,
# local Read-Host prompt; no environment/argument password input is exposed.
function Read-Host { param([string]$Prompt, [switch]$AsSecureString)
    if (-not $AsSecureString) { throw 'Unexpected plaintext prompt' }
    return ConvertTo-SecureString 'integration fixture password, never a real secret' -AsPlainText -Force
}
try {
    $initialized = (& $signer -Action Initialize) | ConvertFrom-Json
    $inspected = (& $signer -Action Inspect) | ConvertFrom-Json
    if ($initialized.keySha256 -cne $inspected.keySha256) { throw 'Fixture identity changed' }; $passed++
    $refused = $false
    try { $null = & $signer -Action Initialize } catch { $refused = $true }
    if (-not $refused) { throw 'Existing custody replaced' }; $passed++
    $code = @'
import base64,json,sys
sys.path.insert(0,sys.argv[1])
from update_admission_test import fixture,statement
bundle,manifest,_proof=fixture()
key=bytes.fromhex(sys.argv[2])
print(json.dumps({'bundle':base64.b64encode(bundle).decode(),'statement':base64.b64encode(statement(manifest,bundle,key)).decode()}))
'@
    $python = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
    $inputs = (& $python -I -B -c $code $portable $initialized.publicKeySpkiHex) | ConvertFrom-Json
    if ($LASTEXITCODE) { throw 'Public fixture preparation failed' }
    $bundle = Join-Path $fixture 'bundle.tar'; $statement = Join-Path $fixture 'statement.json'; $signature = Join-Path $fixture 'signature.bin'
    [IO.File]::WriteAllBytes($bundle, [Convert]::FromBase64String($inputs.bundle))
    [IO.File]::WriteAllBytes($statement, [Convert]::FromBase64String($inputs.statement))
    $signed = (& $signer -Action Sign -BundlePath $bundle -StatementPath $statement -OutputPath $signature) | ConvertFrom-Json
    if (-not $signed.releaseSigned -or (Get-Item -LiteralPath $signature).Length -ne 64) { throw 'Fixture signing failed' }; $passed++
    $verify = @'
const c=require('node:crypto'),f=require('node:fs');
const key=c.createPublicKey({key:Buffer.from(process.argv[1],'hex'),format:'der',type:'spki'});
if(!c.verify(null,f.readFileSync(process.argv[2]),key,f.readFileSync(process.argv[3])))process.exit(1);
'@
    & (Join-Path $env:ProgramFiles 'nodejs/node.exe') -e $verify $initialized.publicKeySpkiHex $statement $signature
    if ($LASTEXITCODE) { throw 'Independent fixture signature verification failed' }; $passed++
    $original = (Get-Item -LiteralPath $signature).LastWriteTimeUtc
    $refused = $false
    try { $null = & $signer -Action Sign -BundlePath $bundle -StatementPath $statement -OutputPath $signature } catch { $refused = $true }
    if (-not $refused -or (Get-Item -LiteralPath $signature).LastWriteTimeUtc -ne $original) { throw 'Existing signature overwritten' }; $passed++
    [IO.File]::WriteAllBytes($bundle, [byte[]]@(1, 2, 3))
    $badOutput = Join-Path $fixture 'must-not-sign.bin'; $refused = $false
    try { $null = & $signer -Action Sign -BundlePath $bundle -StatementPath $statement -OutputPath $badOutput } catch { $refused = $true }
    if (-not $refused -or (Test-Path -LiteralPath $badOutput)) { throw 'Altered bundle signed' }; $passed++
    $backup = Join-Path $parent 'portable-backup/fixture.json'
    $backed = (& $signer -Action Backup -OutputPath $backup) | ConvertFrom-Json
    if (-not $backed.portableEncryptedBackupCreated -or $backed.offlineBackupCreated) { throw 'Backup reporting failed' }; $passed++
    # There is no DPAPI custody at this redirected location. VerifyBackup must
    # recover/sign in memory using ONLY backup+password+recorded public identity.
    $withoutCustody = [scriptblock]::Create($source.Replace($replacements['$Parent = Join-Path $HOME ''.droiddeck-signing'''],
        ('$Parent = ''' + (Join-Path $fixture 'no-custody') + "'")))
    $verified = (& $withoutCustody -Action VerifyBackup -BackupPath $backup -ExpectedPublicKeySha256 $initialized.keySha256) | ConvertFrom-Json
    if (-not $verified.backupRecoveryVerified -or $verified.dpapiUsed -or $verified.privateKeyRestoredToDisk) { throw 'Portable recovery failed' }; $passed++
    [ordered]@{kind='windows-signer-integration';passed=$passed;productionKeyUsed=$false;
        fixturePlaintextPrivateFilesWritten=0;portableRecoveryWithoutDpapi=$true} | ConvertTo-Json -Compress
} finally {
    $resolved = [IO.Path]::GetFullPath($fixture)
    $allowed = [IO.Path]::GetFullPath($FixtureParent).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -notmatch '^droiddeck-signer-integration-[a-f0-9]{32}$') { throw 'Unsafe fixture cleanup refused' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
