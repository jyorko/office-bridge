param([Parameter(Mandatory=$true)][string]$RequestFile,[Parameter(Mandatory=$true)][string]$ResultFile)
$ErrorActionPreference='Stop'
$utf8=New-Object System.Text.UTF8Encoding($false)
$mutex=$null;$owned=$false;$app=$null;$ns=$null;$item=$null
function Fail([string]$code){throw [InvalidOperationException]::new($code)}
function Hash-Text([string]$value){
 $sha=[Security.Cryptography.SHA256]::Create()
 try{return ([BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($value)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
}
function Safe-Name([string]$name,[int]$index){
 if($index -lt 1 -or $index -gt 500){Fail 'invalid_input'}
 # No path separators, ADS, control characters, device names or trailing dots survive.
 $clean=($name -replace '[\x00-\x1f<>:"/\\|?*]','_').Trim(' ','.')
 if(-not $clean){$clean='attachment'}
 if($clean.Length -gt 120){
  $extension=[IO.Path]::GetExtension($clean)
  if($extension.Length -gt 20){$extension=''}
  $clean=$clean.Substring(0,120-$extension.Length)+$extension
 }
 return ('{0:D2}_{1}' -f $index,$clean)
}
function Private-Directory([string]$path){
 if(Test-Path -LiteralPath $path){
  $existing=Get-Item -LiteralPath $path -Force
  if(-not $existing.PSIsContainer -or ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)){Fail 'unsafe_cache_path'}
 }else{[void][IO.Directory]::CreateDirectory($path)}
 $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 & icacls.exe $path /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
 if($LASTEXITCODE -ne 0){Fail 'unsafe_cache_path'}
}
function Assert-PlainFile([string]$path){
 if(Test-Path -LiteralPath $path){
  $file=Get-Item -LiteralPath $path -Force
  if($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)){Fail 'unsafe_cache_path'}
 }
}
function Check-Limits([int]$count,[long]$size){
 if($count -lt 0 -or $count -gt 25 -or $size -lt 0 -or $size -gt 52428800){Fail 'attachment_limit_exceeded'}
}
try{
 $request=[IO.File]::ReadAllText($RequestFile,$utf8) | ConvertFrom-Json
 $a=$request.arguments
 if($request.version -ne 1 -or $request.operation -cne 'download_attachments' -or -not $a.entry_id -or -not $a.store_id -or $a.entry_id.Length -gt 4096 -or $a.store_id.Length -gt 4096){Fail 'invalid_input'}
 $mutex=New-Object Threading.Mutex($false,'Local\CodexOfficeAutomationDesktop')
 try{$owned=$mutex.WaitOne(5000)}catch [Threading.AbandonedMutexException]{$owned=$true}
 if(-not $owned){Fail 'desktop_busy'}
 try{$app=New-Object -ComObject Outlook.Application;$ns=$app.GetNamespace('MAPI')}catch{Fail 'app_unavailable'}
 try{$item=$ns.GetItemFromID([string]$a.entry_id,[string]$a.store_id)}catch{Fail 'not_found'}
 if($null -eq $item -or $item.Class -ne 43 -or $item.EntryID -cne $a.entry_id -or $item.Parent.StoreID -cne $a.store_id){Fail 'not_found'}
 $count=[int]$item.Attachments.Count
 if($count -gt 500){Fail 'attachment_limit_exceeded'}
 $total=0L;$attachments=@()
 for($i=1;$i -le $count;$i++){
  $attachment=$item.Attachments.Item($i);$size=[long]$attachment.Size
  if($a.pdf_only -and [IO.Path]::GetExtension([string]$attachment.FileName) -ine '.pdf'){continue}
  if($size -lt 0){Fail 'attachment_limit_exceeded'}
  $total+=$size;Check-Limits ($attachments.Count+1) $total
  $attachments+=,@{index=$i;attachment=$attachment;filename=[string]$attachment.FileName;size=$size}
 }
 $count=$attachments.Count
 $base=Join-Path $env:LOCALAPPDATA 'OfficeActionServer';Private-Directory $base
 $root=Join-Path $base 'attachments';Private-Directory $root
 $version=([datetime]$item.LastModificationTime).ToUniversalTime().Ticks.ToString()
 $key=Hash-Text (ConvertTo-Json -Compress -InputObject @([string]$a.store_id,[string]$a.entry_id,$version))
 $folder=Join-Path $root $key;Private-Directory $folder
 $rows=@();$savedTotal=0L
 foreach($record in $attachments){
  $name=Safe-Name $record.filename $record.index
   $temp=Join-Path $folder ('.'+[guid]::NewGuid().ToString('N')+'.tmp')
   try{
    $record.attachment.SaveAsFile($temp)
    $actual=[long](Get-Item -LiteralPath $temp).Length
    Check-Limits $count ($savedTotal+$actual)
    $digest=(Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash.ToLowerInvariant()
    $path=Join-Path $folder ($digest+'_'+$name)
    Assert-PlainFile $path
    if(Test-Path -LiteralPath $path){
     if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $digest){Fail 'attachment_cache_conflict'}
    }else{[IO.File]::Move($temp,$path)}
   }finally{if(Test-Path -LiteralPath $temp){[IO.File]::Delete($temp)}}
  $savedTotal+=$actual
  $rows+=,@{index=$record.index;filename=$record.filename;size_bytes=$actual;local_path=$path;sha256=$digest}
 }
 $envelope=@{version=1;ok=$true;result=@{ref=@{entry_id=[string]$item.EntryID;store_id=[string]$item.Parent.StoreID};items=$rows}}
}catch{
 $code=$_.Exception.Message
 if($code -notin @('invalid_input','desktop_busy','app_unavailable','not_found','attachment_limit_exceeded','unsafe_cache_path','attachment_size_changed','attachment_cache_conflict')){$code='attachment_download_failed'}
 $envelope=@{version=1;ok=$false;error=$code}
 if($env:OFFICE_ATTACHMENT_DIAGNOSTIC -eq '1'){$envelope.diagnostic=@{type=$_.Exception.GetType().Name;line=$_.InvocationInfo.ScriptLineNumber;message=$_.Exception.Message}}
}finally{
 if($owned){$mutex.ReleaseMutex()};if($mutex){$mutex.Dispose()}
 foreach($com in @($item,$ns,$app)){if($null -ne $com -and [Runtime.InteropServices.Marshal]::IsComObject($com)){[void][Runtime.InteropServices.Marshal]::ReleaseComObject($com)}}
}
[IO.File]::WriteAllText($ResultFile,($envelope | ConvertTo-Json -Compress -Depth 8),$utf8)
