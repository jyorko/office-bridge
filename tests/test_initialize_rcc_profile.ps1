$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '..\powershell\initialize-rcc.ps1') -HelpersOnly

function Assert-Condition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$firstHomeToken = Get-ProfileToken -ActionsHome 'C:\office-bridge\primary'
$sameHomeToken = Get-ProfileToken -ActionsHome 'c:\office-bridge\primary'
$overrideHomeToken = Get-ProfileToken -ActionsHome 'C:\office-bridge\override'
Assert-Condition ($firstHomeToken -eq $sameHomeToken) 'Equivalent Windows ACTIONS_HOME paths did not share a backup token.'
Assert-Condition ($firstHomeToken -ne $overrideHomeToken) 'Different ACTIONS_HOME overrides shared a backup token.'

$originalBytes = [byte[]](1, 2, 3, 4)
$missingBytes = [byte[]](5, 6, 7, 8)
$originalPem = [Convert]::ToBase64String($originalBytes)
$missingPem = [Convert]::ToBase64String($missingBytes)
$missingCertificate = [pscustomobject]@{
    Id = Get-CertificateId -RawData $missingBytes
    Pem = "-----BEGIN CERTIFICATE-----`n$missingPem`n-----END CERTIFICATE-----"
}

$lines = [System.Collections.Generic.List[string]]::new()
@(
    '',
    'name: original-profile',
    'description: retain this profile',
    'settings:',
    '  certificates:',
    '    verify-ssl: false',
    '    ssl-no-revoke: false',
    '    custom-setting: retain-this-setting',
    '  meta:',
    '    name: original-profile',
    '    source: retain-this-source',
    'ca-bundle: |+',
    '  -----BEGIN CERTIFICATE-----',
    "  $originalPem",
    '  -----END CERTIFICATE-----',
    'custom-root: retain-this-field',
    ''
) | ForEach-Object { $lines.Add($_) }

Set-RccCertificateSetting -Lines $lines -Name 'verify-ssl' -Value 'true'
Set-RccCertificateSetting -Lines $lines -Name 'ssl-no-revoke' -Value 'true'
Set-RccCertificateSetting -Lines $lines -Name 'legacy-renegotiation-allowed' -Value 'false'

$known = Add-MissingWindowsRoots -Lines $lines -Roots @($missingCertificate)
$firstBundle = Get-ProfileBundleInfo -Lines $lines
$verifiedIds = Get-PemCertificateIds -Text $firstBundle.Text

Assert-Condition ($known.ContainsKey((Get-CertificateId -RawData $originalBytes))) 'Existing CA certificate was lost.'
Assert-Condition ($known.ContainsKey($missingCertificate.Id)) 'Windows root certificate was not added.'
Assert-Condition ($verifiedIds.ContainsKey((Get-CertificateId -RawData $originalBytes))) 'Original CA bundle was not retained.'
Assert-Condition ($verifiedIds.ContainsKey($missingCertificate.Id)) 'Added Windows root is missing from the profile.'
Assert-Condition (($lines -contains '    verify-ssl: true')) 'verify-ssl was not enabled.'
Assert-Condition (($lines -contains '    ssl-no-revoke: true')) 'ssl-no-revoke was not enabled.'
Assert-Condition (($lines -contains '    legacy-renegotiation-allowed: false')) 'Legacy renegotiation was not disabled.'
Assert-Condition (($lines -contains 'name: original-profile')) 'The active profile name was not retained.'
Assert-Condition (($lines -contains '    custom-setting: retain-this-setting')) 'An unrelated certificate setting changed.'
Assert-Condition (($lines -contains '    source: retain-this-source')) 'Unrelated profile metadata changed.'
Assert-Condition (($lines -contains 'custom-root: retain-this-field')) 'An unrelated profile field changed.'

$null = Add-MissingWindowsRoots -Lines $lines -Roots @($missingCertificate)
$finalBundle = Get-ProfileBundleInfo -Lines $lines
$certificateBlocks = [regex]::Matches($finalBundle.Text, '-----BEGIN CERTIFICATE-----').Count
Assert-Condition ($certificateBlocks -eq 2) 'Repeated bootstrap duplicated a certificate.'

$minimal = [System.Collections.Generic.List[string]]::new()
@(
    'name: minimal-profile',
    'ca-bundle: ""',
    'other: retain-this-field'
) | ForEach-Object { $minimal.Add($_) }
Set-RccCertificateSetting -Lines $minimal -Name 'verify-ssl' -Value 'true'
Set-RccCertificateSetting -Lines $minimal -Name 'ssl-no-revoke' -Value 'true'
Set-RccCertificateSetting -Lines $minimal -Name 'legacy-renegotiation-allowed' -Value 'false'
$null = Add-MissingWindowsRoots -Lines $minimal -Roots @($missingCertificate)
$minimalBundle = Get-ProfileBundleInfo -Lines $minimal
$minimalIds = Get-PemCertificateIds -Text $minimalBundle.Text
Assert-Condition ($minimalIds.ContainsKey($missingCertificate.Id)) 'A root could not be added to an empty bundle.'
Assert-Condition (($minimal -contains 'name: minimal-profile')) 'An empty profile name changed.'
Assert-Condition (($minimal -contains 'other: retain-this-field')) 'An empty profile lost an unrelated field.'
