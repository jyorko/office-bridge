param([Parameter(Mandatory=$true)][string]$RequestFile,[Parameter(Mandatory=$true)][string]$ResultFile)
$ErrorActionPreference='Stop'
$utf8=New-Object System.Text.UTF8Encoding($false)
$owned=$false; $mutex=$null
function Fail([string]$code){throw [System.InvalidOperationException]::new($code)}
function Stamp($date){([datetime]$date).ToUniversalTime().ToString('o')}
function Hash([string]$value){$sha=[Security.Cryptography.SHA256]::Create();try{([BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($value)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
function Teams-Windows {
 $root=[System.Windows.Automation.AutomationElement]::RootElement
 return @($root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object {([string]$_.Current.Name).StartsWith('Chat | ',[StringComparison]::Ordinal) -and ([string]$_.Current.Name).EndsWith('Microsoft Teams',[StringComparison]::Ordinal)})
}
function Get-Teams {
 $windows=@(Teams-Windows)
 if($windows.Count -gt 1){Fail 'ambiguous_chat'}
 if($windows.Count -eq 0){
  # A running meeting or an unavailable main view must never trigger another app instance.
  if(@(Get-Process -Name 'ms-teams' -ErrorAction SilentlyContinue).Count){Fail 'app_not_ready'}
  $packages=@(Get-AppxPackage -Name MSTeams -ErrorAction SilentlyContinue)
  if($packages.Count -ne 1){Fail 'app_unavailable'}
  $executable=Join-Path $packages[0].InstallLocation 'ms-teams.exe'
  if(-not (Test-Path -LiteralPath $executable -PathType Leaf)){Fail 'app_unavailable'}
  Start-Process -FilePath $executable -WindowStyle Minimized | Out-Null
  for($i=0;$i -lt 40;$i++){Start-Sleep -Milliseconds 250;$windows=@(Teams-Windows);if($windows.Count){break}}
  if($windows.Count -ne 1){Fail 'app_not_ready'}
 }
 return $windows[0]
}
function Foreground-Handle {
 if(-not ('OfficeDesktopNative' -as [type])){Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class OfficeDesktopNative { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }'}
 return [OfficeDesktopNative]::GetForegroundWindow().ToInt64()
}
function Assert-NotEditing([long]$mainHandle,[long]$foregroundHandle,[bool]$editable){
 if($mainHandle -eq $foregroundHandle -and $editable){Fail 'desktop_busy'}
}
function Assert-NavigationAllowed([long]$mainHandle,[long]$foregroundHandle,[bool]$alreadyActive){
 if(-not $alreadyActive -and $mainHandle -ne $foregroundHandle){Fail 'navigation_requires_foreground'}
}
function Assert-DesktopIdle($window){
 $foreground=Foreground-Handle
 if($foreground -ne [long]$window.Current.NativeWindowHandle){return}
 $focused=[System.Windows.Automation.AutomationElement]::FocusedElement
 if(-not $focused){return}
 $editable=($focused.Current.ControlType.ProgrammaticName -eq 'ControlType.Edit')
 $pattern=$null
 if($focused.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)){$editable=$editable -or -not ([System.Windows.Automation.ValuePattern]$pattern).Current.IsReadOnly}
 Assert-NotEditing ([long]$window.Current.NativeWindowHandle) $foreground $editable
}
function Elements($window){return @($window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))}
function Chat-Name([string]$label){
 if($label -notmatch '^(Chat|Meeting chat|Group chat) ' -or $label -notmatch ' Has pinned messages| Last message'){return $null}
 $name=($label -split ' Has pinned messages| Last message')[0] -replace '^(Chat|Meeting chat|Group chat) ',''
 if($label.StartsWith('Chat ',[StringComparison]::Ordinal)){$name=$name -replace ' (Available|Busy|Do not disturb|Be right back|Away|Offline|Out of office|In a call|In a meeting|Presenting)$',''}
 return $name
}
function Chat-Elements($window){return @(Elements $window | Where-Object {$_.Current.ControlType.ProgrammaticName -eq 'ControlType.TreeItem' -and (Chat-Name ([string]$_.Current.Name))})}
function Chat-Key($window,[string]$label){Hash ("$($window.Current.NativeWindowHandle)`n$label")}
function Bodies($window){return @(Elements $window | Where-Object {([string]$_.Current.AutomationId).StartsWith('message-body-',[StringComparison]::Ordinal)} | ForEach-Object {@{observed_id=[string]$_.Current.AutomationId;text=[string]$_.Current.Name}})}
try{
 $request=[IO.File]::ReadAllText($RequestFile,$utf8) | ConvertFrom-Json
 if($request.version -ne 1){Fail 'invalid_input'}
 $a=$request.arguments; $op=[string]$request.operation
 $allowed=@('list_chats','read_chat')
 if($op -notin $allowed){Fail 'invalid_input'}
 $mutex=New-Object Threading.Mutex($false,'Local\CodexOfficeAutomationDesktop')
 try{$owned=$mutex.WaitOne(5000)}catch [Threading.AbandonedMutexException]{$owned=$true}
 if(-not $owned){Fail 'desktop_busy'}
 Add-Type -AssemblyName UIAutomationClient;Add-Type -AssemblyName UIAutomationTypes
 if($a.PSObject.Properties.Name -contains 'limit'){if([int]$a.limit -lt 1 -or [int]$a.limit -gt 50){Fail 'invalid_input'}}
 switch($op){
  'list_chats' {
   $window=Get-Teams;$chats=@(Chat-Elements $window)
   $rows=@();foreach($chat in @($chats | Select-Object -First ([int]$a.limit))){$label=[string]$chat.Current.Name;if($label){$rows+=,@{ref=@{snapshot_id=(Chat-Key $window $label);exact_tree_label=$label;window_title=[string]$window.Current.Name};display_name=(Chat-Name $label)}}}
   $result=@{items=$rows;scope='currently_loaded_chat_list';captured_at=(Stamp (Get-Date));truncated=($chats.Count -gt [int]$a.limit)}
  }
  'read_chat' {
   $window=Get-Teams;$label=[string]$a.chat.exact_tree_label
   if((Chat-Key $window $label) -cne $a.chat.snapshot_id){Fail 'stale_chat'}
   $visibleChats=@(Chat-Elements $window)
   $matches=@($visibleChats | Where-Object {$_.Current.Name -ceq $label})
   if($matches.Count -ne 1){Fail 'ambiguous_chat'}
   $name=Chat-Name $label
   if(@($visibleChats | Where-Object {(Chat-Name ([string]$_.Current.Name)) -ceq $name}).Count -ne 1){Fail 'ambiguous_chat'}
   $prefix='Chat | '+$name+' |';$already=$window.Current.Name.StartsWith($prefix,[StringComparison]::Ordinal)
   $before=ConvertTo-Json -Compress -Depth 4 -InputObject @(Bodies $window)
   Assert-DesktopIdle $window;$foregroundBefore=Foreground-Handle
   Assert-NavigationAllowed ([long]$window.Current.NativeWindowHandle) $foregroundBefore $already
   if(-not $already){
    ([System.Windows.Automation.SelectionItemPattern]$matches[0].GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)).Select()
    if((Foreground-Handle) -ne $foregroundBefore){Fail 'desktop_busy'}
   }
   $previous='';$settled=$false;$fresh=$already
   for($i=0;$i -lt 35;$i++){
    Start-Sleep -Milliseconds 200;$window=Get-Teams
    if(-not $window.Current.Name.StartsWith($prefix,[StringComparison]::Ordinal)){continue}
    $messages=@(Bodies $window);$current=ConvertTo-Json -Compress -Depth 4 -InputObject $messages
    $fresh=$already -or ($current -cne $before -and $messages.Count -gt 0)
    if($fresh -and $current -ceq $previous){$settled=$true;break};$previous=$current
   }
   if(-not $settled){Fail 'chat_navigation_unverified'}
   $rows=@();$chars=0;$truncated=($messages.Count -gt [int]$a.limit)
   foreach($message in @($messages | Select-Object -Last ([int]$a.limit))){$remaining=80000-$chars;if($remaining -le 0){$truncated=$true;break};if($message.text.Length -gt $remaining){$message.text=$message.text.Substring(0,$remaining);$truncated=$true};$chars+=$message.text.Length;$rows+=,$message}
   $result=@{chat=$a.chat;messages=$rows;scope='currently_loaded_history';captured_at=(Stamp (Get-Date));truncated=$truncated}
  }

 }
 $envelope=@{version=1;ok=$true;result=$result}
}catch{
 $code=$_.Exception.Message
 $known=@('invalid_input','desktop_busy','navigation_requires_foreground','ambiguous_chat','stale_chat','chat_navigation_unverified','app_unavailable','app_not_ready')
 if($code -notin $known){$code='desktop_operation_failed'}
 $envelope=@{version=1;ok=$false;error=$code}
}finally{
 if($owned){$mutex.ReleaseMutex()};if($mutex){$mutex.Dispose()}
}
$json=ConvertTo-Json -Compress -Depth 15 -InputObject $envelope
if($utf8.GetByteCount($json) -gt 1048576){$json='{"version":1,"ok":false,"error":"output_overflow"}'}
[IO.File]::WriteAllText($ResultFile,$json,$utf8)
