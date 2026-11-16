function Repair-ProcessEnvironment {
    # Windows names are case-insensitive, but inherited blocks can contain both
    # Path and PATH. Windows PowerShell's Start-Process rejects that block.
    $variables = [Environment]::GetEnvironmentVariables('Process')
    $duplicates = @($variables.Keys | Group-Object | Where-Object { $_.Count -gt 1 })
    foreach ($group in $duplicates) {
        $name = [string]$group.Group[0]
        $value = [Environment]::GetEnvironmentVariable($name, 'Process')
        foreach ($entry in $group.Group) {
            [Environment]::SetEnvironmentVariable([string]$entry, $null, 'Process')
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}
