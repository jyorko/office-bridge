function Get-YamlIndent {
    param([Parameter(Mandatory)][string]$Line)

    if ($Line -match '^\t') {
        throw 'RCC exported YAML contains tab indentation; refusing to rewrite the profile.'
    }
    if ($Line -match '^( *)') { return $Matches[1].Length }
    return 0
}

function Find-YamlMappingIndex {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)][int]$Start,
        [Parameter(Mandatory)][int]$End,
        [Parameter(Mandatory)][int]$Indent,
        [Parameter(Mandatory)][string]$Name
    )

    $escapedName = [regex]::Escape($Name)
    for ($index = $Start; $index -lt $End; $index++) {
        $line = $Lines[$index]
        if ($line -match "^(?<spaces> *)$escapedName\s*:(?<tail>.*)$" -and $Matches.spaces.Length -eq $Indent) {
            return $index
        }
    }
    return -1
}

function Get-YamlSectionEnd {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)][int]$Start,
        [Parameter(Mandatory)][int]$Indent
    )

    for ($index = $Start + 1; $index -lt $Lines.Count; $index++) {
        $line = $Lines[$index]
        if ([string]::IsNullOrWhiteSpace($line) -or $line -match '^\s*#') { continue }
        if ($line -match '^\s*(---|\.\.\.)\s*$') { return $index }
        if ((Get-YamlIndent -Line $line) -le $Indent) { return $index }
    }
    return $Lines.Count
}

function Get-YamlSection {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)][int]$ParentStart,
        [Parameter(Mandatory)][int]$ParentEnd,
        [Parameter(Mandatory)][int]$ParentIndent,
        [Parameter(Mandatory)][string]$Name,
        [switch]$Create
    )

    $indent = $ParentIndent + 2
    $index = Find-YamlMappingIndex -Lines $Lines -Start $ParentStart -End $ParentEnd -Indent $indent -Name $Name
    if ($index -lt 0) {
        if (-not $Create) { return $null }
        $Lines.Insert($ParentEnd, (' ' * $indent) + $Name + ':')
        $index = $ParentEnd
    }

    $line = $Lines[$index]
    $escapedName = [regex]::Escape($Name)
    if ($line -notmatch "^(?<spaces> *)$escapedName\s*:(?<tail>.*)$" -or $Matches.tail.Trim() -ne '') {
        throw "RCC exported YAML field '$Name' is not a mapping; refusing to rewrite the profile."
    }

    return [pscustomobject]@{
        Index = $index
        Indent = $indent
        End = Get-YamlSectionEnd -Lines $Lines -Start $index -Indent $indent
    }
}

function Set-YamlScalar {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)]$Section,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )

    $indent = $Section.Indent + 2
    $index = Find-YamlMappingIndex -Lines $Lines -Start ($Section.Index + 1) -End $Section.End -Indent $indent -Name $Name
    $replacement = (' ' * $indent) + $Name + ': ' + $Value
    if ($index -ge 0) {
        $Lines[$index] = $replacement
    } else {
        $Lines.Insert($Section.End, $replacement)
    }
}

function Set-RccCertificateSetting {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )

    $rootSection = Get-YamlSection -Lines $Lines -ParentStart 0 -ParentEnd $Lines.Count -ParentIndent -2 -Name 'settings' -Create
    $certificateSection = Get-YamlSection -Lines $Lines -ParentStart ($rootSection.Index + 1) -ParentEnd $rootSection.End -ParentIndent $rootSection.Indent -Name 'certificates' -Create
    Set-YamlScalar -Lines $Lines -Section $certificateSection -Name $Name -Value $Value
}

function Get-CertificateId {
    param([Parameter(Mandatory)][byte[]]$RawData)

    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash($RawData)).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-ProfileToken {
    param([Parameter(Mandatory)][string]$ActionsHome)

    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $normalized = [IO.Path]::GetFullPath($ActionsHome).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar).ToLowerInvariant()
        $bytes = [Text.Encoding]::UTF8.GetBytes($normalized)
        return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').Substring(0, 16).ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-PemCertificateIds {
    param([AllowEmptyString()][string]$Text)

    $ids = @{}
    $pattern = '-----BEGIN CERTIFICATE-----\s*(?<body>.*?)\s*-----END CERTIFICATE-----'
    foreach ($match in [regex]::Matches($Text, $pattern, [Text.RegularExpressions.RegexOptions]::Singleline)) {
        try {
            $encoded = $match.Groups['body'].Value -replace '\s', ''
            $rawData = [Convert]::FromBase64String($encoded)
            $ids[(Get-CertificateId -RawData $rawData)] = $true
        } catch [FormatException] {
            continue
        }
    }
    return $ids
}

function Get-ProfileBundleInfo {
    param([Parameter(Mandatory)][AllowEmptyString()][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines)

    $index = Find-YamlMappingIndex -Lines $Lines -Start 0 -End $Lines.Count -Indent 0 -Name 'ca-bundle'
    if ($index -lt 0) {
        $index = $Lines.Count
        $Lines.Add('ca-bundle: |')
        $bodyStart = $index + 1
        $bodyEnd = $bodyStart
        $bodyIndent = 2
        return [pscustomobject]@{
            Index = $index
            End = $bodyEnd
            BodyStart = $bodyStart
            BodyEnd = $bodyEnd
            BodyIndent = $bodyIndent
            Text = ''
        }
    }

    $line = $Lines[$index]
    $tail = $line -replace '^ca-bundle\s*:', ''
    $tail = $tail.Trim()
    if ($tail -match '^\|(?:[+-]?[1-9]?|[1-9]?[+-]?)$') {
    } elseif ($tail -in @('', '""', "''", 'null', '~')) {
        $Lines[$index] = 'ca-bundle: |'
    } else {
        throw 'RCC exported YAML has a non-block ca-bundle value; refusing to rewrite the profile.'
    }

    $bodyStart = $index + 1
    $bodyEnd = $Lines.Count
    for ($cursor = $bodyStart; $cursor -lt $Lines.Count; $cursor++) {
        $bodyLine = $Lines[$cursor]
        if ([string]::IsNullOrWhiteSpace($bodyLine)) { continue }
        if ((Get-YamlIndent -Line $bodyLine) -eq 0) {
            $bodyEnd = $cursor
            break
        }
    }

    $bodyIndent = 0
    for ($cursor = $bodyStart; $cursor -lt $bodyEnd; $cursor++) {
        $bodyLine = $Lines[$cursor]
        if ([string]::IsNullOrWhiteSpace($bodyLine)) { continue }
        $lineIndent = Get-YamlIndent -Line $bodyLine
        if ($bodyIndent -eq 0 -or $lineIndent -lt $bodyIndent) { $bodyIndent = $lineIndent }
    }
    if ($bodyIndent -eq 0) { $bodyIndent = 2 }

    $bodyText = [System.Collections.Generic.List[string]]::new()
    for ($cursor = $bodyStart; $cursor -lt $bodyEnd; $cursor++) {
        $bodyLine = $Lines[$cursor]
        if ($bodyLine.Length -ge $bodyIndent) {
            $bodyText.Add($bodyLine.Substring($bodyIndent))
        } elseif ([string]::IsNullOrWhiteSpace($bodyLine)) {
            $bodyText.Add('')
        } else {
            throw 'RCC exported YAML has invalid ca-bundle indentation; refusing to rewrite the profile.'
        }
    }

    return [pscustomobject]@{
        Index = $index
        End = $bodyEnd
        BodyStart = $bodyStart
        BodyEnd = $bodyEnd
        BodyIndent = $bodyIndent
        Text = $bodyText -join "`n"
    }
}

function Add-MissingWindowsRoots {
    param(
        [Parameter(Mandatory)][AllowEmptyString()][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Lines,
        [Parameter(Mandatory)][object[]]$Roots
    )

    $bundle = Get-ProfileBundleInfo -Lines $Lines
    $known = Get-PemCertificateIds -Text $bundle.Text
    $addLines = [System.Collections.Generic.List[string]]::new()
    foreach ($root in $Roots) {
        if ($known.ContainsKey($root.Id)) { continue }
        foreach ($pemLine in ($root.Pem -split "`n")) {
            $addLines.Add((' ' * $bundle.BodyIndent) + $pemLine)
        }
        $known[$root.Id] = $true
    }

    if ($addLines.Count -gt 0) {
        $Lines.InsertRange($bundle.End, [string[]]$addLines.ToArray())
    }
    return $known
}

function Set-PrivatePermissions {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$Directory
    )

    $suffix = if ($Directory) { ':(OI)(CI)F' } else { ':F' }
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $userGrant = '*' + $userSid + $suffix
    $systemGrant = '*S-1-5-18' + $suffix
    & icacls.exe $Path '/inheritance:r' '/grant:r' $userGrant $systemGrant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict private RCC profile access at '$Path'." }
}

function Invoke-RccBootstrap {
    param([Parameter(Mandatory)][string]$Root)

    $release = Get-Content (Join-Path $root 'scripts\runtime-release.json') -Raw | ConvertFrom-Json
    $server = Join-Path $env:USERPROFILE '.local\bin\action-server.exe'
    $state = Join-Path $root '.state'
    $rcc = Join-Path $env:LOCALAPPDATA "OfficeActionServer\bin\rcc-runtime-$($release.version).exe"
    $appHome = if ([string]::IsNullOrWhiteSpace($env:ACTIONS_HOME)) {
        Join-Path $env:LOCALAPPDATA 'OfficeActionServer\rcc-actions'
    } else {
        [IO.Path]::GetFullPath($env:ACTIONS_HOME)
    }

    if (-not (Test-Path -LiteralPath $server)) { throw 'Pinned Actions Runtime executable is not installed.' }
    New-Item -ItemType Directory -Force -Path $state, $appHome | Out-Null
    Set-PrivatePermissions -Path $state -Directory

    $profileToken = Get-ProfileToken -ActionsHome $appHome
    $backupPath = Join-Path $state "actions-rcc-$profileToken-active-profile.before.yaml"
    $exportPath = Join-Path $state "actions-rcc-$profileToken-active-profile.export.yaml"
    $mergedPath = Join-Path $state "actions-rcc-$profileToken-active-profile.merged.yaml"
    $verifiedPath = Join-Path $state "actions-rcc-$profileToken-active-profile.verified.yaml"
    $oldActionsHome = $env:ACTIONS_HOME
    $oldRobocorpHome = $env:ROBOCORP_HOME

    try {
        $env:ACTIONS_HOME = $appHome
        $env:ROBOCORP_HOME = $appHome
        if (-not (Test-Path -LiteralPath $rcc)) {
            & $server download-rcc --file $rcc
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $rcc)) {
                throw 'Official Actions Runtime could not download its pinned RCC helper.'
            }
        }

        $diagnosticsRaw = & $rcc configuration diagnostics --quick --json
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the application RCC profile.' }
        $diagnostics = ($diagnosticsRaw -join "`n") | ConvertFrom-Json
        $activeProfile = $diagnostics.details.'config-active-profile'
        if ([string]::IsNullOrWhiteSpace($activeProfile)) { $activeProfile = 'default' }

        if ($activeProfile -eq 'default' -and
            -not (Test-Path -LiteralPath (Join-Path $appHome 'profile_default.yaml'))) {
            # RCC's built-in default has no exportable profile on a fresh home.
            $defaultProfile = @(
                'name: default',
                'description: Windows trusted roots; SSL verification enabled.',
                'settings:',
                '  meta:',
                '    name: default',
                '    source: Office Bridge Windows certificate stores',
                '    version: "1"',
                'ca-bundle: ""'
            ) -join "`n"
            [IO.File]::WriteAllText($exportPath, $defaultProfile + "`n", [Text.UTF8Encoding]::new($false))
        } else {
            & $rcc configuration export --profile $activeProfile --filename $exportPath | Out-Null
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exportPath)) {
                throw "Could not export active RCC profile '$activeProfile'."
            }
        }
        Set-PrivatePermissions -Path $exportPath
        if (-not (Test-Path -LiteralPath $backupPath)) {
            Copy-Item -LiteralPath $exportPath -Destination $backupPath
            Set-PrivatePermissions -Path $backupPath
        }

        $lines = [System.Collections.Generic.List[string]]::new()
        foreach ($line in [IO.File]::ReadAllLines($exportPath)) { $lines.Add($line) }
        Set-RccCertificateSetting -Lines $lines -Name 'verify-ssl' -Value 'true'
        Set-RccCertificateSetting -Lines $lines -Name 'ssl-no-revoke' -Value 'true'

        $trustedRoots = @(Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root | Sort-Object Thumbprint -Unique)
        if ($trustedRoots.Count -lt 10) { throw 'Could not read the Windows trusted root certificate stores.' }
        $roots = [System.Collections.Generic.List[object]]::new()
        foreach ($certificate in $trustedRoots) {
            $encoded = [Convert]::ToBase64String($certificate.RawData, [Base64FormattingOptions]::InsertLineBreaks) -replace '\s', ''
            $encodedLines = for ($offset = 0; $offset -lt $encoded.Length; $offset += 64) {
                $encoded.Substring($offset, [Math]::Min(64, $encoded.Length - $offset))
            }
            $pem = "-----BEGIN CERTIFICATE-----`n$($encodedLines -join "`n")`n-----END CERTIFICATE-----"
            $roots.Add([pscustomobject]@{
                Id = Get-CertificateId -RawData $certificate.RawData
                Pem = $pem
            })
        }
        $rootIds = @{}
        foreach ($certificate in $roots) { $rootIds[$certificate.Id] = $true }
        $null = Add-MissingWindowsRoots -Lines $lines -Roots $roots.ToArray()

        $mergedText = ($lines -join [Environment]::NewLine) + [Environment]::NewLine
        [IO.File]::WriteAllText($mergedPath, $mergedText, [Text.UTF8Encoding]::new($false))
        Set-PrivatePermissions -Path $mergedPath

        $importAttempted = $false
        try {
            $importAttempted = $true
            & $rcc configuration import --filename $mergedPath --switch
            if ($LASTEXITCODE -ne 0) { throw 'Could not import the merged RCC trust profile.' }

            $afterRaw = & $rcc configuration diagnostics --quick --json
            if ($LASTEXITCODE -ne 0) { throw 'Could not verify the active RCC trust profile.' }
            $after = ($afterRaw -join "`n") | ConvertFrom-Json
            $afterProfile = $after.details.'config-active-profile'
            if ($afterProfile -ne $activeProfile) {
                throw "RCC active profile changed unexpectedly from '$activeProfile' to '$afterProfile'."
            }

            $settingsRaw = & $rcc configuration settings --json
            if ($LASTEXITCODE -ne 0) { throw 'Could not verify RCC certificate settings.' }
            $settings = ($settingsRaw -join "`n") | ConvertFrom-Json
            if ($settings.certificates.'verify-ssl' -ne $true -or
                $settings.certificates.'ssl-no-revoke' -ne $true) {
                throw 'RCC certificate verification settings did not retain the required values.'
            }

            & $rcc configuration export --profile $activeProfile --filename $verifiedPath | Out-Null
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $verifiedPath)) {
                throw 'Could not verify the imported RCC CA bundle.'
            }
            Set-PrivatePermissions -Path $verifiedPath
            $verifiedLines = [System.Collections.Generic.List[string]]::new()
            foreach ($line in [IO.File]::ReadAllLines($verifiedPath)) { $verifiedLines.Add($line) }
            $verifiedBundle = Get-ProfileBundleInfo -Lines $verifiedLines
            $verifiedIds = Get-PemCertificateIds -Text $verifiedBundle.Text
            foreach ($rootId in $rootIds.Keys) {
                if (-not $verifiedIds.ContainsKey($rootId)) {
                    throw 'The imported RCC CA bundle is missing a Windows trusted root.'
                }
            }
        } catch {
            $failure = $_
            if ($importAttempted) {
                & $rcc configuration import --filename $exportPath --switch | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    throw "RCC trust setup failed and the original active profile could not be restored: $($failure.Exception.Message)"
                }
            }
            throw $failure
        }

        Write-Output "Prepared pinned RCC $rcc; active profile retained as '$activeProfile'."
        Write-Output "Application RCC home: $appHome"
        Write-Output "Private profile backup: $backupPath"
    } finally {
        if ($null -eq $oldActionsHome) { Remove-Item Env:ACTIONS_HOME -ErrorAction SilentlyContinue } else { $env:ACTIONS_HOME = $oldActionsHome }
        if ($null -eq $oldRobocorpHome) { Remove-Item Env:ROBOCORP_HOME -ErrorAction SilentlyContinue } else { $env:ROBOCORP_HOME = $oldRobocorpHome }
        foreach ($temporaryPath in @($exportPath, $mergedPath, $verifiedPath)) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}
