using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

// The window is never shown; the STA message loop owns all WebView2 objects.
public sealed class TeamsSession : ApplicationContext
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern bool SetDllDirectory(string path);
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool GetNamedPipeClientComputerName(
        Microsoft.Win32.SafeHandles.SafePipeHandle pipe, StringBuilder name, uint size);
    const int MaxFrame = 1048576;
    static readonly JavaScriptSerializer Json =
        new JavaScriptSerializer { MaxJsonLength = MaxFrame };
    readonly Form host = new Form();
    readonly string sdk, profile, pipeName, generation, expectedAccount;
    readonly int parentId;
    readonly CancellationTokenSource stop = new CancellationTokenSource();
    CoreWebView2Environment environment;
    CoreWebView2Controller controller;
    FileStream profileLock;
    System.Windows.Forms.Timer foregroundTimer;
    bool initializing, interference;
    string error = "loading";
    string currentAccountName;
    string groupScript;
    string scriptPath, nativePath, scriptDigest, nativeDigest;

    const string Surface = @"(() => {
      const dialog=document.querySelector('[data-tid=me-control-menu-dialog]');
      const menu=dialog && dialog.querySelector('[data-tid=me-control-menu]');
      const emails=menu ? Array.from(new Set((menu.innerText.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi)||[]).map(e=>e.toLowerCase()))) : [];
      const name=dialog && dialog.querySelector('[data-tid=me-control-displayname]')?.textContent.trim() || '';
      const verified=!!dialog && dialog.getAttribute('role')==='dialog' && dialog.getAttribute('aria-label')==='Profile menu' && !!menu && menu.getAttribute('role')==='menu' && !!dialog.querySelector('[data-tid=me-control-sign-out-button]') && emails.length===1 && emails[0]===__EXPECTED_ACCOUNT__;
      const fields=dialog ? Array.from(dialog.querySelectorAll('[data-tid]')).map(e=>e.getAttribute('data-tid')).slice(0,30) : [];
      return {hostname:location.hostname,path:location.pathname,app:!!document.querySelector('[data-tid=experience-layout]') && !!document.querySelector('[data-tid=me-control-avatar-trigger]'),login:!!document.querySelector('input[type=email],input[name=loginfmt],input[type=password]'),blocked:!!document.querySelector('[data-tid=unauthorized-page],[data-tid=error-page]'),account_verified:verified,account_name:name,profile_fields:fields};
    })()";

    TeamsSession(string sdkPath, string profilePath, string pipe, string gen, int parent)
    {
        sdk = sdkPath;
        profile = profilePath;
        pipeName = pipe;
        generation = gen;
        parentId = parent;
        expectedAccount = (Environment.GetEnvironmentVariable("OFFICE_TEAMS_EXPECTED_ACCOUNT") ?? "")
                              .Trim()
                              .ToLowerInvariant();
        host.ShowInTaskbar = false;
        host.ClientSize = new Size(1200, 900);
        var handle = host.Handle;
        foregroundTimer = new System.Windows.Forms.Timer { Interval = 500 };
        foregroundTimer.Tick += (s, e) =>
        {
            if (host.Visible || OwnedForeground())
                interference = true;
        };
        foregroundTimer.Start();
        host.BeginInvoke(new Action(async () => await Initialize()));
        Task.Run(() => Serve());
        Task.Run(() => MonitorParent());
    }
    async Task Initialize()
    {
        if (initializing)
            return;
        initializing = true;
        try
        {
            string lockPath =
                Path.Combine(Path.GetDirectoryName(profile), "teams-webview2-profile.lock");
            Directory.CreateDirectory(Path.GetDirectoryName(profile));
            profileLock = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite,
                                         FileShare.ReadWrite);
            profileLock.Lock(0, 1);
            SetDllDirectory(sdk);
            var options =
                new CoreWebView2EnvironmentOptions { AllowSingleSignOnUsingOSPrimaryAccount =
                                                         true };
            environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
            controller = await environment.CreateCoreWebView2ControllerAsync(host.Handle);
            controller.Bounds = new Rectangle(0, 0, 1200, 900);
            controller.IsVisible = true;
            var core = controller.CoreWebView2;
            core.Settings.IsWebMessageEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.AreDefaultScriptDialogsEnabled = false;
            core.NewWindowRequested += (s, e) =>
            { e.Handled = true; };
            core.LaunchingExternalUriScheme += (s, e) =>
            { e.Cancel = true; };
            core.PermissionRequested += (s, e) =>
            {
                e.State = CoreWebView2PermissionState.Deny;
                e.Handled = true;
            };
            core.BasicAuthenticationRequested += (s, e) =>
            { e.Cancel = true; };
            core.ProcessFailed += (s, e) =>
            {
                error = "browser_failed";
                host.BeginInvoke(new Action(() => ExitThread()));
            };
            scriptPath = Environment.GetEnvironmentVariable("OFFICE_TEAMS_SCRIPT_FILE");
            nativePath = Environment.GetEnvironmentVariable("OFFICE_TEAMS_HOST_SOURCE");
            if (!String.IsNullOrEmpty(scriptPath) && File.Exists(scriptPath))
                groupScript = File.ReadAllText(scriptPath);
            scriptDigest = FileDigest(scriptPath);
            nativeDigest = FileDigest(nativePath);
            if (String.IsNullOrEmpty(expectedAccount) ||
                !Regex.IsMatch(expectedAccount, "^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$"))
                throw new InvalidOperationException("expected_account_unconfigured");
            core.Navigate("https://teams.cloud.microsoft/");
            error = "loading";
        }
        catch (IOException)
        {
            error = "profile_busy";
        }
        catch (Exception)
        {
            error = "host_unavailable";
        }
        finally
        {
            initializing = false;
        }
    }
    bool OwnedForeground()
    {
        IntPtr foreground = GetForegroundWindow();
        if (foreground == host.Handle)
            return true;
        uint pid;
        GetWindowThreadProcessId(foreground, out pid);
        if (pid == Process.GetCurrentProcess().Id)
            return true;
        if (environment != null)
            foreach (var info in environment.GetProcessInfos())
                if (info.ProcessId == pid)
                    return true;
        return false;
    }
    async Task<Dictionary<string, object>> Inspect()
    {
        string surface = Surface.Replace("__EXPECTED_ACCOUNT__", Json.Serialize(expectedAccount));
        string raw = await controller.CoreWebView2.ExecuteScriptAsync(surface);
        return Json.Deserialize<Dictionary<string, object>>(raw);
    }
    static bool Flag(Dictionary<string, object> value, string name)
    {
        object x;
        return value.TryGetValue(name, out x) && x is bool && (bool)x;
    }
    static string FileDigest(string path)
    {
        if (String.IsNullOrEmpty(path) || !File.Exists(path))
            return null;
        using (var sha = SHA256.Create()) using (
            var file = File.OpenRead(path)) return BitConverter.ToString(sha.ComputeHash(file));
    }
    async Task<Dictionary<string, object>> Status()
    {
        IntPtr initialForeground = GetForegroundWindow();
        string state = error, hostname = "teams.cloud.microsoft", path = "/";
        bool account = false;
        var samples = new List<Dictionary<string, object>>();
        if (host.Visible || OwnedForeground())
            interference = true;
        if (controller != null && !interference && error != "browser_failed")
        {
            var page = await Inspect();
            hostname = Convert.ToString(page["hostname"]);
            path = Convert.ToString(page["path"]);
            if (Flag(page, "blocked"))
                state = "access_blocked";
            else if (Flag(page, "login"))
                state = "needs_interactive_login";
            else if (Flag(page, "app"))
            {
                await controller.CoreWebView2.ExecuteScriptAsync(
                    "(() => { if(!document.querySelector('[data-tid=me-control-menu-dialog]')) " +
                    "document.querySelector('[data-tid=me-control-avatar-trigger]')?.click(); " +
                    "})()");
                page = await Inspect();
                object fields;
                if (page.TryGetValue("profile_fields", out fields))
                {
                    samples.Add(new Dictionary<string, object> {
                        { "account_email_verified", Flag(page, "account_verified") },
                        { "profile_fields", fields }
                    });
                }
                string profileName = Convert.ToString(page["account_name"]);
                account = Flag(page, "account_verified") && !String.IsNullOrWhiteSpace(profileName);
                if (account) currentAccountName = profileName;
                await controller.CoreWebView2.ExecuteScriptAsync(
                    "(() => { if(document.querySelector('[data-tid=me-control-menu-dialog]')) " +
                    "document.querySelector('[data-tid=me-control-avatar-trigger]')?.click(); " +
                    "})()");
                state = account ? "authenticated" : "loading";
            }
            else
                state = "loading";
        }
        string reason = null;
        bool restartRequired = true;
        try
        {
            restartRequired = scriptDigest == null || nativeDigest == null ||
                              scriptDigest != FileDigest(scriptPath) ||
                              nativeDigest != FileDigest(nativePath);
        }
        catch (IOException)
        {
        }
        if (interference)
        {
            state = "failed";
            reason = "foreground_interference";
        }
        else if (state == "host_unavailable" || state == "browser_failed" ||
                 state == "profile_busy")
        {
            reason = state;
            state = "failed";
        }
        return new Dictionary<string, object> { { "state", state },
                                                { "hostname", hostname },
                                                { "path", path },
                                                { "backend", "webview2" },
                                                { "account_verified", account },
                                                { "host_visible", host.Visible },
                                                { "control_visible", IsWindowVisible(host.Handle) },
                                                { "host_focused", OwnedForeground() },
                                                { "host_owned", true },
                                                { "foreground_interference", interference },
                                                { "foreground_unchanged",
                                                  initialForeground == GetForegroundWindow() },
                                                { "generation", generation },
                                                { "restart_required", restartRequired },
                                                { "samples", samples.Count > 0 && !account ? samples : null },
                                                { "reason", reason } };
    }
    async Task<Dictionary<string, object>> Dispatch(Dictionary<string, object> request)
    {
        object version, operation;
        if (!request.TryGetValue("version", out version) || Convert.ToInt32(version) != 1 ||
            !request.TryGetValue("operation", out operation))
            throw new InvalidDataException();
        string op = Convert.ToString(operation);
        if (op == "teams_web_status")
            return await Status();
        if (op == "shutdown")
        {
            object raw;
            object token;
            if (!request.TryGetValue("arguments", out raw))
                throw new InvalidDataException();
            var args = raw as Dictionary<string, object>;
            if (args == null || !args.TryGetValue("token", out token) ||
                Convert.ToString(token) !=
                    Environment.GetEnvironmentVariable("OFFICE_TEAMS_SHUTDOWN_TOKEN"))
                throw new InvalidDataException();
            return new Dictionary<string, object> { { "shutdown", true } };
        }
        if (op == "teams_web_list_chats" || op == "teams_web_read_chat" ||
            op == "teams_web_send_chat")
        {
            object raw;
            if (!request.TryGetValue("arguments", out raw))
                throw new InvalidDataException();
            var args = raw as Dictionary<string, object>;
            if (args == null)
                throw new InvalidDataException();
            if (op == "teams_web_list_chats")
                return await ListChats(args);
            if (op == "teams_web_read_chat")
                return await Read(args);
            return await Send(args);
        }
        throw new InvalidDataException();
    }
    async Task<Dictionary<string, object>> ListChats(Dictionary<string, object> args)
    {
        object rawLimit, rawName;
        if (String.IsNullOrEmpty(groupScript) || !args.TryGetValue("limit", out rawLimit))
            throw new InvalidDataException();
        int limit = Convert.ToInt32(rawLimit);
        string name = args.TryGetValue("name", out rawName) ? rawName as string : null;
        if (limit < 1 || limit > 100 || (rawName != null && name == null) ||
            (name != null && (name.Length < 1 || name.Length > 120)))
            throw new InvalidDataException();
        var current = await Status();
        if (Convert.ToString(current["state"]) != "authenticated" || interference)
            throw new InvalidOperationException();
        string navigationId = Guid.NewGuid().ToString("N");
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (DateTime.UtcNow < deadline)
        {
            if (host.Visible || OwnedForeground())
            {
                interference = true;
                throw new InvalidOperationException();
            }
            var request = new Dictionary<string, object> {
                { "phase", "list" }, { "name", name }, { "limit", limit },
                { "navigationId", navigationId }
            };
            var result = Json.Deserialize<Dictionary<string, object>>(
                await controller.CoreWebView2.ExecuteScriptAsync("(" + groupScript + ")(" +
                    Json.Serialize(request) + ")"));
            string state = Convert.ToString(result["state"]);
            if (state == "listed")
            {
                var items = result["items"] as System.Collections.IList;
                if (items == null || items.Count > limit)
                    throw new InvalidOperationException();
                foreach (var item in items)
                {
                    var refValue = item as Dictionary<string, object>;
                    if (refValue == null || !refValue.ContainsKey("chat_id") ||
                        !refValue.ContainsKey("title") ||
                        !ValidChatId(Convert.ToString(refValue["chat_id"])) ||
                        String.IsNullOrWhiteSpace(Convert.ToString(refValue["title"])) ||
                        Convert.ToString(refValue["title"]).Length > 120 ||
                        (name != null && Convert.ToString(refValue["title"]) != name))
                        throw new InvalidOperationException();
                }
                current = await Status();
                if (Convert.ToString(current["state"]) != "authenticated")
                    throw new InvalidOperationException();
                return result;
            }
            if (state != "waiting")
                throw new InvalidOperationException(state);
            await Task.Delay(150);
        }
        throw new TimeoutException();
    }
    static bool ValidChatId(string chatId)
    {
        return chatId == "48:notes" || Regex.IsMatch(
            chatId ?? "", "^19:[A-Za-z0-9._:-]{1,256}@thread\\.(?:v2|skype)$");
    }
    async Task<Dictionary<string, object>> Read(Dictionary<string, object> args)
    {
        object chatValue, limitValue;
        if (!args.TryGetValue("chat", out chatValue) ||
            !args.TryGetValue("limit", out limitValue) || String.IsNullOrEmpty(groupScript))
            throw new InvalidDataException();
        var chat = chatValue as Dictionary<string, object>;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidDataException();
        string chatId = Convert.ToString(chat["chat_id"]), title = Convert.ToString(chat["title"]);
        int limit = Convert.ToInt32(limitValue);
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120 ||
            limit < 1 || limit > 50)
            throw new InvalidDataException();
        var current = await Status();
        if (Convert.ToString(current["state"]) != "authenticated" || interference)
            throw new InvalidOperationException();
        string phase = "locate", navigationId = Guid.NewGuid().ToString("N");
        var deadline = DateTime.UtcNow.AddSeconds(25);
        while (DateTime.UtcNow < deadline)
        {
            if (host.Visible || OwnedForeground())
            {
                interference = true;
                throw new InvalidOperationException();
            }
            var request = new Dictionary<string, object> {
                { "phase", phase }, { "chatId", chatId }, { "title", title },
                { "limit", limit }, { "navigationId", navigationId }
            };
            var result = Json.Deserialize<Dictionary<string, object>>(
                await controller.CoreWebView2.ExecuteScriptAsync("(" + groupScript + ")(" +
                    Json.Serialize(request) + ")"));
            string state = Convert.ToString(result["state"]);
            if (state == "read")
            {
                if (Convert.ToString(result["chat_id"]) != chatId ||
                    Convert.ToString(result["title"]) != title)
                    throw new InvalidOperationException();
                current = await Status();
                if (Convert.ToString(current["state"]) != "authenticated")
                    throw new InvalidOperationException();
                return result;
            }
            if (state == "ready" || state == "navigated") phase = "read";
            else if (state == "navigate") phase = "navigate";
            else if (state != "waiting") throw new InvalidOperationException(state);
            await Task.Delay(150);
        }
        throw new TimeoutException();
    }
    async Task<Dictionary<string, object>> Send(Dictionary<string, object> args)
    {
        object chatValue, bodyValue, requestValue;
        if (!args.TryGetValue("chat", out chatValue) || !args.TryGetValue("body", out bodyValue) ||
            !args.TryGetValue("request_id", out requestValue) || String.IsNullOrEmpty(groupScript))
            throw new InvalidDataException();
        var chat = chatValue as Dictionary<string, object>;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidDataException();
        string chatId = Convert.ToString(chat["chat_id"]), title = Convert.ToString(chat["title"]),
               body = bodyValue as string, requestId = requestValue as string;
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120 ||
            String.IsNullOrEmpty(body) || body.Length > 2000 || String.IsNullOrEmpty(requestId) ||
            !Regex.IsMatch(requestId, "^[A-Za-z0-9._-]{1,128}$"))
            throw new InvalidDataException();
        var current = await Status();
        if (Convert.ToString(current["state"]) != "authenticated" || interference ||
            Flag(current, "restart_required") || String.IsNullOrWhiteSpace(currentAccountName))
            throw new InvalidOperationException();
        string claims = Path.Combine(Path.GetDirectoryName(profile), "teams-web-sends", "claims");
        Directory.CreateDirectory(claims);
        string digest, bodyDigest, targetDigest;
        using (var sha = SHA256.Create())
        {
            digest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(requestId)))
                         .Replace("-", "")
                         .ToLowerInvariant();
            bodyDigest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(body)))
                             .Replace("-", "")
                             .ToLowerInvariant();
            targetDigest = BitConverter.ToString(
                               sha.ComputeHash(Encoding.UTF8.GetBytes(chatId + "\0" + title)))
                               .Replace("-", "")
                               .ToLowerInvariant();
        }
        string claim = Path.Combine(claims, digest + ".json");
        using (var file = new FileStream(claim, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(
                new Dictionary<string, object> { { "body_sha256", bodyDigest },
                                                 { "destination_sha256", targetDigest } }));
            file.Write(bytes, 0, bytes.Length);
            file.Flush(true);
        }
        string phase = "navigate", navigationId = Guid.NewGuid().ToString("N");
        string authorName = currentAccountName;
        var deadline = DateTime.UtcNow.AddSeconds(35);
        while (DateTime.UtcNow < deadline)
        {
            if (host.Visible || OwnedForeground())
            {
                interference = true;
                throw new InvalidOperationException();
            }
            if (phase == "prepare" || phase == "send" || phase == "observe")
            {
                current = await Status();
                if (Convert.ToString(current["state"]) != "authenticated" ||
                    Flag(current, "restart_required") ||
                    currentAccountName != authorName)
                    throw new InvalidOperationException();
            }
            var request = new Dictionary<string, object> {
                { "phase", phase }, { "body", body }, { "chatId", chatId },
                { "title", title }, { "requestId", requestId }, { "authorName", authorName },
                { "navigationId", navigationId }
            };
            var result = Json.Deserialize<Dictionary<string, object>>(
                await controller.CoreWebView2.ExecuteScriptAsync("(" + groupScript + ")( " +
                    Json.Serialize(request) + ")"));
            string state = Convert.ToString(result["state"]);
            if (state == "navigated") phase = "prepare";
            else if (state == "prepared")
            {
                await controller.CoreWebView2.CallDevToolsProtocolMethodAsync(
                    "Input.insertText", Json.Serialize(new Dictionary<string, object> { { "text", body } }));
                phase = "send";
            }
            else if (state == "clicked") phase = "observe";
            else if (state == "observed")
            {
                if (Convert.ToString(result["chat_id"]) != chatId ||
                    Convert.ToString(result["title"]) != title)
                    throw new InvalidOperationException();
                current = await Status();
                if (Convert.ToString(current["state"]) != "authenticated" ||
                    currentAccountName != authorName)
                    throw new InvalidOperationException();
                string receipt = claim + ".receipt";
                using (var file = new FileStream(receipt, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(result));
                    file.Write(bytes, 0, bytes.Length);
                    file.Flush(true);
                }
                return result;
            }
            else if (state != "waiting") throw new InvalidOperationException(state);
            await Task.Delay(150);
        }
        throw new TimeoutException();
    }
    async Task<Dictionary<string, object>> OnSta(Dictionary<string, object> request)
    {
        var done = new TaskCompletionSource<Dictionary<string, object>>();
        host.BeginInvoke(new Action(async () =>
                                    {
                                        try
                                        {
                                            done.SetResult(await Dispatch(request));
                                        }
                                        catch (Exception error)
                                        {
                                            done.SetException(error);
                                        }
                                    }));
        return await done.Task;
    }
    static async Task<byte[]> ReadExact(Stream stream, int length, CancellationToken token)
    {
        byte[] data = new byte[length];
        int offset = 0;
        while (offset < length)
        {
            int n = await stream.ReadAsync(data, offset, length - offset, token);
            if (n == 0)
                throw new EndOfStreamException();
            offset += n;
        }
        return data;
    }
    async Task Serve()
    {
        var acl = new PipeSecurity();
        acl.SetAccessRuleProtection(true, false);
        acl.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,
                                             PipeAccessRights.FullControl,
                                             AccessControlType.Allow));
        acl.AddAccessRule(
            new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                               PipeAccessRights.FullControl, AccessControlType.Allow));
        while (!stop.IsCancellationRequested)
        {
            try
            {
                using (var pipe = new NamedPipeServerStream(
                           pipeName, PipeDirection.InOut, 1, PipeTransmissionMode.Byte,
                           PipeOptions.Asynchronous, 65536, 65536, acl))
                {
                    await pipe.WaitForConnectionAsync(stop.Token);
                    var clientMachine = new StringBuilder(256);
                    bool named = GetNamedPipeClientComputerName(pipe.SafePipeHandle, clientMachine,
                                                                (uint)clientMachine.Capacity);
                    int nameError = Marshal.GetLastWin32Error();
                    if (named || nameError != 229)
                        throw new InvalidDataException();
                    Dictionary<string, object> request;
                    using (var deadline =
                               CancellationTokenSource.CreateLinkedTokenSource(stop.Token))
                    {
                        deadline.CancelAfter(10000);
                        byte[] prefix = await ReadExact(pipe, 4, deadline.Token);
                        int size = BitConverter.ToInt32(prefix, 0);
                        if (size < 2 || size > MaxFrame)
                            throw new InvalidDataException();
                        request = Json.Deserialize<Dictionary<string, object>>(
                            Encoding.UTF8.GetString(await ReadExact(pipe, size, deadline.Token)));
                    }
                    Dictionary<string, object> response;
                    try
                    {
                        response = new Dictionary<string, object> {
                            { "version", 1 }, { "ok", true }, { "result", await OnSta(request) }
                        };
                    }
                    catch (Exception error)
                    {
                        string reason = error.Message;
                        if (reason != "draft_exists" && reason != "identity_failed" &&
                            reason != "failed")
                            reason = "host_operation_failed";
                        Console.Error.WriteLine("Teams session operation failed: " + reason + " (" +
                                                error.GetType().Name + ")");
                        response = new Dictionary<string, object> { { "version", 1 },
                                                                    { "ok", false },
                                                                    { "error", reason } };
                    }
                    byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(response));
                    byte[] header = BitConverter.GetBytes(bytes.Length);
                    await pipe.WriteAsync(header, 0, header.Length, stop.Token);
                    await pipe.WriteAsync(bytes, 0, bytes.Length, stop.Token);
                    await pipe.FlushAsync(stop.Token);
                    object operation;
                    if (request.TryGetValue("operation", out operation) &&
                        Convert.ToString(operation) == "shutdown" && (bool)response["ok"])
                        host.BeginInvoke(new Action(() => ExitThread()));
                }
            }
            catch (OperationCanceledException)
            {
                if (stop.IsCancellationRequested)
                    break;
            }
            catch (Exception)
            {
                Thread.Sleep(100);
            }
        }
    }
    void MonitorParent()
    {
        try
        {
            using (var parent = Process.GetProcessById(parentId)) parent.WaitForExit();
        }
        catch
        {
        }
        try
        {
            host.BeginInvoke(new Action(() => ExitThread()));
        }
        catch
        {
        }
    }
    protected override void ExitThreadCore()
    {
        stop.Cancel();
        if (foregroundTimer != null)
            foregroundTimer.Dispose();
        if (controller != null)
            controller.Close();
        if (profileLock != null)
        {
            try
            {
                profileLock.Unlock(0, 1);
            }
            catch
            {
            }
            profileLock.Dispose();
        }
        host.Dispose();
        base.ExitThreadCore();
    }
    public static void Run(string sdk, string profile, string pipe, string generation, int parent)
    {
        Application.Run(new TeamsSession(sdk, profile, pipe, generation, parent));
    }
}
