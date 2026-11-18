param([Parameter(Mandatory=$true)][string]$RequestFile,[Parameter(Mandatory=$true)][string]$ResultFile)
$ErrorActionPreference='Stop'
$utf8=New-Object System.Text.UTF8Encoding($false)
$owned=$false; $mutex=$null; $app=$null; $ns=$null; $mutationStarted=$false
function Fail([string]$code){throw [System.InvalidOperationException]::new($code)}
function Stamp($date){([datetime]$date).ToUniversalTime().ToString('o')}
function Hash([string]$value){$sha=[Security.Cryptography.SHA256]::Create();try{([BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($value)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
function Ref($item){@{entry_id=[string]$item.EntryID;store_id=[string]$item.Parent.StoreID}}
function Importance($item){@('low','normal','high')[[int]$item.Importance]}
function Version($item){@{modified_at=(Stamp $item.LastModificationTime);subject=[string]$item.Subject;unread=[bool]$item.UnRead;folder_entry_id=[string]$item.Parent.EntryID;flagged=([int]$item.FlagStatus -ne 0);importance=(Importance $item)}}
function Address($entry,[string]$name,[int]$type=1){
 $smtp=$null
 try{if($entry.Type -eq 'EX'){$user=$entry.GetExchangeUser();if($user){$smtp=$user.PrimarySmtpAddress}else{$list=$entry.GetExchangeDistributionList();if($list){$smtp=$list.PrimarySmtpAddress}}}else{$smtp=$entry.Address}}catch{}
 @{display_name=$name;smtp_address=$smtp;recipient_type=$type}
}
function Recipients($item){$rows=@();for($i=1;$i -le $item.Recipients.Count;$i++){$r=$item.Recipients.Item($i);$rows+=,(Address $r.AddressEntry ([string]$r.Name) ([int]$r.Type))};return $rows}
function Summary($item){@{ref=(Ref $item);version=(Version $item);sender=(Address $item.Sender ([string]$item.SenderName));subject=[string]$item.Subject;received_at=(Stamp $item.ReceivedTime);unread=[bool]$item.UnRead;flagged=([int]$item.FlagStatus -ne 0);importance=(Importance $item)}}
function Get-Mail($reference){
 if(-not $reference.entry_id -or -not $reference.store_id){Fail 'invalid_input'}
 try{$item=$ns.GetItemFromID([string]$reference.entry_id,[string]$reference.store_id)}catch{Fail 'not_found'}
 if($null -eq $item -or $item.Class -ne 43){Fail 'not_found'}
 if($item.Parent.StoreID -cne $reference.store_id -or $item.EntryID -cne $reference.entry_id){Fail 'not_found'}
 return $item
}
function Check-Version($item,$expected){
 $actual=Version $item
 if(-not $expected -or ([datetime]$actual.modified_at).ToUniversalTime() -ne ([datetime]$expected.modified_at).ToUniversalTime()){Fail 'stale_message'}
 foreach($key in @('subject','unread','folder_entry_id','flagged','importance')){if($actual[$key] -cne $expected.$key){Fail 'stale_message'}}
}
function Effective-Account($item){
 $explicit=$item.SendUsingAccount
 if($explicit -and [string]$explicit.SmtpAddress){return $explicit}
 $accounts=@($ns.Accounts | Where-Object {$_.DeliveryStore.StoreID -ceq $item.Parent.StoreID -and [string]$_.SmtpAddress})
 if($accounts.Count -ne 1){Fail 'stale_draft'}
 return $accounts[0]
}
function Digest($item){
 if($item.BodyFormat -ne 1 -or $item.Attachments.Count -ne 0){Fail 'stale_draft'}
 $recipients=@(Recipients $item)
 if($recipients.Count -lt 1 -or @($recipients | Where-Object {-not $_.smtp_address -or $_.smtp_address -notmatch '^[^@\s]+@[^@\s]+$'}).Count){Fail 'unresolved_recipient'}
 $addresses=@($recipients | ForEach-Object {"$($_.recipient_type):$($_.smtp_address)"})
 $account=[string](Effective-Account $item).SmtpAddress
 if(-not $account){Fail 'stale_draft'}
 Hash (ConvertTo-Json -Compress -Depth 6 -InputObject @([string]$item.Subject,[string]$item.Body,$addresses,$account,[string]$item.SentOnBehalfOfName))
}
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
function Assert-ConfiguredAccount([string]$identity,[string]$recipient){
 $configured=[string]$env:OFFICE_BRIDGE_ACCOUNT
 if($configured -notmatch '^[^@\s]+@[^@\s]+$' -or $identity -ine $configured -or $recipient -ine $identity){Fail 'self_identity_unverified'}
}
try{
 $request=[IO.File]::ReadAllText($RequestFile,$utf8) | ConvertFrom-Json
 if($request.version -ne 1){Fail 'invalid_input'}
 $a=$request.arguments; $op=[string]$request.operation
 $allowed=@('inbox_list','get_message','search_inbox','create_reply_draft','create_mail_draft','send_draft','archive','list_chats','read_chat')
 if($op -notin $allowed){Fail 'invalid_input'}
 $mutex=New-Object Threading.Mutex($false,'Local\CodexOfficeAutomationDesktop')
 try{$owned=$mutex.WaitOne(5000)}catch [Threading.AbandonedMutexException]{$owned=$true}
 if(-not $owned){Fail 'desktop_busy'}
 if($op -in @('list_chats','read_chat')){Add-Type -AssemblyName UIAutomationClient;Add-Type -AssemblyName UIAutomationTypes}else{
  try{$app=New-Object -ComObject Outlook.Application;$ns=$app.GetNamespace('MAPI')}catch{Fail 'app_unavailable'}
 }
 if($a.PSObject.Properties.Name -contains 'limit'){if([int]$a.limit -lt 1 -or [int]$a.limit -gt 50){Fail 'invalid_input'}}
 switch($op){
  {$_ -in @('inbox_list','search_inbox')} {
   $inbox=$ns.GetDefaultFolder(6);$items=$inbox.Items;$items.Sort('[ReceivedTime]',$true)
   $rows=@();$scanned=0;$total=$items.Count
   for($index=1;$index -le $total -and $scanned -lt 500 -and $rows.Count -lt [int]$a.limit;$index++){
    $item=$items.Item($index);$scanned++;if($item.Class -ne 43){continue}
    if(($a.unread_only -or $a.query.unread_only) -and -not $item.UnRead){continue}
    $q=$a.query
    if($q.subject_contains -and ([string]$item.Subject).IndexOf([string]$q.subject_contains,[StringComparison]::OrdinalIgnoreCase) -lt 0){continue}
    if($q.sender_contains -and (([string]$item.SenderName)+' '+([string]$item.SenderEmailAddress)).IndexOf([string]$q.sender_contains,[StringComparison]::OrdinalIgnoreCase) -lt 0){continue}
    if($q.received_after -and ([datetime]$item.ReceivedTime).ToUniversalTime() -le ([datetime]$q.received_after).ToUniversalTime()){continue}
    $rows+=,(Summary $item)
   }
   $result=@{items=$rows;scanned_count=$scanned;truncated=($scanned -lt $total)}
  }
  'get_message' {
   $item=Get-Mail $a.message;$result=Summary $item;$body=[string]$item.Body
   $result.recipients=@(Recipients $item);$result.body_truncated=($body.Length -gt 20000);$result.body_text=$body.Substring(0,[Math]::Min($body.Length,20000))
  }
  'create_reply_draft' {
   $item=Get-Mail $a.source;Check-Version $item $a.expected_source
   if(-not $a.body_text -or $a.body_text.Length -gt 20000){Fail 'invalid_input'}
   $accounts=@($ns.Accounts | Where-Object {$_.DeliveryStore.StoreID -ceq $item.Parent.StoreID})
   if($accounts.Count -ne 1){Fail 'unresolved_recipient'}
   $mutationStarted=$true
   if($a.reply_all){$reply=$item.ReplyAll()}else{$reply=$item.Reply()}
   [void]$reply.GetType().InvokeMember('SendUsingAccount',[Reflection.BindingFlags]::SetProperty,$null,$reply,@($accounts[0].PSObject.BaseObject))
   if(-not $reply.Recipients.ResolveAll()){Fail 'unresolved_recipient'}
   $reply.BodyFormat=1;$reply.Body=[string]$a.body_text
   while($reply.Attachments.Count -gt 0){$reply.Attachments.Item(1).Delete()}
   [void](Digest $reply);$reply.Save()
   $digest=Digest $reply
   $result=@{ref=(Ref $reply);source=(Ref $item);recipients=@(Recipients $reply);subject=[string]$reply.Subject;body_text=[string]$reply.Body;sending_account=[string](Effective-Account $reply).SmtpAddress;digest=$digest}
  }
  'create_mail_draft' {
   if(-not $a.body_text -or $a.body_text.Length -gt 20000 -or -not $a.subject -or $a.subject.Length -gt 250 -or $a.subject -match '[\r\n]'){Fail 'invalid_input'}
   $identity=(Address $ns.CurrentUser.AddressEntry ([string]$ns.CurrentUser.Name)).smtp_address
   Assert-ConfiguredAccount $identity ([string]$a.recipient_email)
   $accounts=@($ns.Accounts | Where-Object {$_.SmtpAddress -ieq $identity})
   if($accounts.Count -ne 1){Fail 'self_identity_unverified'}
   $mutationStarted=$true;$mail=$app.CreateItem(0)
   [void]$mail.GetType().InvokeMember('SendUsingAccount',[Reflection.BindingFlags]::SetProperty,$null,$mail,@($accounts[0].PSObject.BaseObject))
   $recipient=$mail.Recipients.Add($identity);$recipient.Type=1
   if(-not $mail.Recipients.ResolveAll()){Fail 'unresolved_recipient'}
   $resolved=@(Recipients $mail)
   if($resolved.Count -ne 1 -or $resolved[0].smtp_address -ine $identity){Fail 'self_identity_unverified'}
   $mail.Subject=[string]$a.subject;$mail.BodyFormat=1;$mail.Body=[string]$a.body_text;[void](Digest $mail);$mail.Save()
   $result=@{ref=(Ref $mail);recipients=@(Recipients $mail);subject=[string]$mail.Subject;body_text=[string]$mail.Body;sending_account=[string](Effective-Account $mail).SmtpAddress;digest=(Digest $mail)}
  }
  'send_draft' {
   $item=Get-Mail $a.draft;$drafts=$ns.GetDefaultFolder(16)
   if($item.Sent -or $item.Submitted -or $item.Parent.EntryID -cne $drafts.EntryID -or $item.Parent.StoreID -cne $drafts.StoreID){Fail 'stale_draft'}
   if(-not $item.Recipients.ResolveAll()){Fail 'unresolved_recipient'}
   $account=Effective-Account $item
   [void]$item.GetType().InvokeMember('SendUsingAccount',[Reflection.BindingFlags]::SetProperty,$null,$item,@($account.PSObject.BaseObject))
   if((Digest $item) -cne $a.expected_digest){Fail 'stale_draft'}
   $subject=[string]$item.Subject
   $body=[string]$item.Body
   $preview=$body.Substring(0,[Math]::Min($body.Length,20000))
   $mutationStarted=$true;$item.Send()
   $result=@{draft=$a.draft;status='submitted';submitted_at=(Stamp (Get-Date));subject=$subject;body_text=$preview;body_truncated=($body.Length -gt 20000)}
  }
  'archive' {
   $item=Get-Mail $a.message;Check-Version $item $a.expected_source;$inbox=$ns.GetDefaultFolder(6)
   if($item.Parent.EntryID -cne $inbox.EntryID -or $item.Parent.StoreID -cne $inbox.StoreID -or $item.FlagStatus -ne 0 -or $item.Importance -eq 2){Fail 'archive_protected'}
   try{$archive=$inbox.Parent.Folders.Item('Archive')}catch{Fail 'archive_unavailable'}
   if(-not $archive -or $archive.StoreID -cne $inbox.StoreID){Fail 'archive_unavailable'}
   $mutationStarted=$true;$moved=$item.Move($archive)
   $result=@{original=$a.message;archived=(Ref $moved);archived_at=(Stamp (Get-Date))}
  }
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
 $known=@('invalid_input','desktop_busy','navigation_requires_foreground','not_found','stale_message','stale_draft','unresolved_recipient','archive_protected','archive_unavailable','ambiguous_chat','stale_chat','chat_navigation_unverified','app_unavailable','app_not_ready','self_identity_unverified')
 if($code -notin $known){$code=if($mutationStarted){'mutation_outcome_unknown'}else{'desktop_operation_failed'}}
 $envelope=@{version=1;ok=$false;error=$code}
}finally{
 foreach($object in @($ns,$app)){if($object -and [Runtime.InteropServices.Marshal]::IsComObject($object)){[void][Runtime.InteropServices.Marshal]::ReleaseComObject($object)}}
 if($owned){$mutex.ReleaseMutex()};if($mutex){$mutex.Dispose()}
}
$json=ConvertTo-Json -Compress -Depth 15 -InputObject $envelope
if($utf8.GetByteCount($json) -gt 1048576){$json='{"version":1,"ok":false,"error":"output_overflow"}'}
[IO.File]::WriteAllText($ResultFile,$json,$utf8)
