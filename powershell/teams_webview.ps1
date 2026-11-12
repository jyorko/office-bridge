param([Parameter(Mandatory=$true)][string]$RequestFile,[Parameter(Mandatory=$true)][string]$ResultFile)
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Write-Envelope($value) {
    [IO.File]::WriteAllText($ResultFile, (ConvertTo-Json -Compress -Depth 8 -InputObject $value), $utf8)
}
try {
    $request = Get-Content -LiteralPath $RequestFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($request.version -ne 1 -or $request.operation -cnotin @('teams_web_status','teams_web_send_group') -or ($request.operation -ceq 'teams_web_status' -and $request.arguments -and @($request.arguments.PSObject.Properties).Count -ne 0) -or ($request.operation -ceq 'teams_web_send_group' -and (-not $request.arguments.body -or $request.arguments.body.Length -gt 2000))) {
        Write-Envelope @{version=1;ok=$false;error='invalid_input'}; exit 0
    }
    $sdk = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\webview2-sdk\1.0.4314.35'
    $core = Join-Path $sdk 'Microsoft.Web.WebView2.Core.dll'
    if (-not (Test-Path -LiteralPath $core -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $sdk 'WebView2Loader.dll') -PathType Leaf)) {
        Write-Envelope @{version=1;ok=$false;error='sdk_unavailable'}; exit 0
    }
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -Path $core
    $code = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

public static class OfficeTeamsWebView {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern bool SetDllDirectory(string path);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static readonly string SurfaceScript = @"(() => {
        const dialog = document.querySelector('[data-tid=me-control-menu-dialog]');
        const menu = dialog && dialog.querySelector('[data-tid=me-control-menu]');
        const emails = menu ? (menu.innerText.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || []) : [];
        const verified = !!dialog && dialog.getAttribute('role') === 'dialog' &&
            dialog.getAttribute('aria-label') === 'Profile menu' && !!menu && menu.getAttribute('role') === 'menu' &&
            !!dialog.querySelector('[data-tid=me-control-sign-out-button]') &&
            emails.some(email => email.toLowerCase() === 'joshua.yorko@gainwelltechnologies.com');
        return {
            hostname: location.hostname, path: location.pathname,
            app: !!document.querySelector('[data-tid=experience-layout]') && !!document.querySelector('[data-tid=me-control-avatar-trigger]'),
            login: !!document.querySelector('input[type=email], input[name=loginfmt], input[type=password]'),
            blocked: !!document.querySelector('[data-tid=unauthorized-page], [data-tid=error-page]'),
            account_verified: verified
        };
    })()";

    static void ProfileMenu(CoreWebView2 core, bool close) {
        string condition = close ? "document.querySelector('[data-tid=me-control-menu-dialog]')" : "!document.querySelector('[data-tid=me-control-menu-dialog]')";
        var task = core.ExecuteScriptAsync("(() => { if (" + condition + ") { const trigger = document.querySelector('[data-tid=me-control-avatar-trigger]'); if (trigger) trigger.click(); } })()");
        Pump(task, 3000);
    }

    static void Pump(Task task, int milliseconds) {
        var deadline = DateTime.UtcNow.AddMilliseconds(milliseconds);
        while (!task.IsCompleted && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(15); }
        if (!task.IsCompleted) throw new TimeoutException();
        task.GetAwaiter().GetResult();
    }

    static Dictionary<string, object> Inspect(CoreWebView2 core) {
        var task = core.ExecuteScriptAsync(SurfaceScript);
        Pump(task, 3000);
        return Json.Deserialize<Dictionary<string, object>>(task.Result);
    }

    static bool Flag(Dictionary<string, object> value, string key) {
        object item;
        return value.TryGetValue(key, out item) && item is bool && (bool)item;
    }

    static bool OwnedForeground(CoreWebView2Environment environment, IntPtr host) {
        var foreground = GetForegroundWindow();
        if (foreground == host) return true;
        uint process;
        GetWindowThreadProcessId(foreground, out process);
        if (process == Process.GetCurrentProcess().Id) return true;
        foreach (var item in environment.GetProcessInfos()) if (item.ProcessId == process) return true;
        return false;
    }

    static Dictionary<string, object> Execute(CoreWebView2 core, string script) {
        var task = core.ExecuteScriptAsync(script); Pump(task, 5000);
        return Json.Deserialize<Dictionary<string, object>>(task.Result);
    }
    static string GroupSend(CoreWebView2 core, CoreWebView2Environment env, IntPtr handle, Form host, string script, string body) {
        var deadline = DateTime.UtcNow.AddSeconds(35);
        string phase = "navigate";
        while (DateTime.UtcNow < deadline) {
            Application.DoEvents();
            if (host.Visible || OwnedForeground(env, handle)) throw new Exception("foreground_interference");
            var result = Execute(core, "(" + script + ")(" + Json.Serialize(phase) + "," + Json.Serialize(body) + ")");
            var state = Convert.ToString(result["state"]);
            if (state == "navigated") phase = "prepare";
            else if (state == "prepared") {
                var insert = core.CallDevToolsProtocolMethodAsync("Input.insertText", Json.Serialize(new Dictionary<string, object> {{"text",body}}));
                Pump(insert, 5000); phase = "send";
            } else if (state == "clicked") phase = "observe";
            else if (state == "observed") return Json.Serialize(result);
            else if (state != "waiting") throw new Exception(state);
            Thread.Sleep(150);
        }
        throw new TimeoutException();
    }
    public static string Run(string profile, string sdk, string operation, string body, string scriptPath) {
        SetDllDirectory(sdk);
        var initialForeground = GetForegroundWindow();
        using (var host = new Form()) {
            host.ShowInTaskbar = false;
            host.ClientSize = new Size(1200, 900);
            var handle = host.Handle;
            var options = new CoreWebView2EnvironmentOptions();
            options.AllowSingleSignOnUsingOSPrimaryAccount = true;
            var environmentTask = CoreWebView2Environment.CreateAsync(null, profile, options);
            Pump(environmentTask, 20000);
            var environment = environmentTask.Result;
            var controllerTask = environment.CreateCoreWebView2ControllerAsync(handle);
            Pump(controllerTask, 20000);
            var controller = controllerTask.Result;
            try {
                controller.Bounds = new Rectangle(0, 0, 1200, 900);
                // Render the page while its containing window remains unshown.
                controller.IsVisible = true;
                var core = controller.CoreWebView2;
                core.NewWindowRequested += (sender, args) => { args.Handled = true; };
                core.LaunchingExternalUriScheme += (sender, args) => { args.Cancel = true; };
                core.PermissionRequested += (sender, args) => { args.State = CoreWebView2PermissionState.Deny; args.Handled = true; };
                core.BasicAuthenticationRequested += (sender, args) => { args.Cancel = true; };
                core.Settings.IsWebMessageEnabled = false;
                core.Settings.AreHostObjectsAllowed = false;
                core.Settings.AreDefaultScriptDialogsEnabled = false;
                core.Navigate("https://teams.cloud.microsoft/");
                string state = "loading", hostname = "teams.cloud.microsoft", path = "/";
                bool account = false, interference = false, profileOpened = false;
                var deadline = DateTime.UtcNow.AddSeconds(40);
                while (DateTime.UtcNow < deadline) {
                    Application.DoEvents();
                    if (host.Visible || OwnedForeground(environment, handle)) { interference = true; state = "failed"; break; }
                    try {
                        var page = Inspect(core);
                        hostname = Convert.ToString(page["hostname"]);
                        path = Convert.ToString(page["path"]);
                        if (Flag(page, "blocked")) { state = "access_blocked"; break; }
                        if (Flag(page, "login")) { state = "needs_interactive_login"; break; }
                        account = Flag(page, "account_verified");
                        if (Flag(page, "app") && account) { state = "authenticated"; break; }
                        if (Flag(page, "app") && !profileOpened) { ProfileMenu(core, false); profileOpened = true; }
                    } catch { }
                    Thread.Sleep(200);
                }
                if (profileOpened) ProfileMenu(core, true);
                if (operation == "teams_web_send_group") {
                    if (state != "authenticated" || interference || host.Visible || OwnedForeground(environment, handle)) throw new Exception("preflight_failed");
                    return GroupSend(core, environment, handle, host, File.ReadAllText(scriptPath), body);
                }
                return Json.Serialize(new Dictionary<string, object> {
                    {"state", state}, {"hostname", hostname}, {"path", path.Length > 200 ? path.Substring(0, 200) : path},
                    {"backend", "webview2"}, {"account_verified", account}, {"host_visible", host.Visible},
                    {"control_visible", IsWindowVisible(handle)}, {"host_focused", OwnedForeground(environment, handle)},
                    {"host_owned", handle != IntPtr.Zero}, {"foreground_interference", interference},
                    {"foreground_unchanged", initialForeground == GetForegroundWindow()}
                });
            } finally { controller.Close(); }
        }
    }
}
'@
    Add-Type -TypeDefinition $code -ReferencedAssemblies @([System.Windows.Forms.Form].Assembly.Location,[System.Drawing.Point].Assembly.Location,$core,'System.Web.Extensions')
    $profile = Join-Path $env:LOCALAPPDATA 'OfficeActionServer\teams-webview2-profile'
    New-Item -ItemType Directory -Force -Path $profile | Out-Null
    $result = [OfficeTeamsWebView]::Run($profile, $sdk, $request.operation, [string]$request.arguments.body, (Join-Path $PSScriptRoot 'teams_group.js')) | ConvertFrom-Json
    Write-Envelope @{version=1;ok=$true;result=$result}
} catch {
    $code = if ($_.Exception.GetBaseException() -is [TimeoutException]) { 'timeout' } else { 'desktop_operation_failed' }
    Write-Envelope @{version=1;ok=$false;error=$code}
}
