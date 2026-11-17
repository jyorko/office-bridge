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
    CoreWebView2Controller auxiliaryController;
    CoreWebView2Controller copilotController;
    Form copilotHost;
    readonly List<CoreWebView2Frame> copilotFrames = new List<CoreWebView2Frame>();
    readonly List<CoreWebView2Frame> auxiliaryFrames = new List<CoreWebView2Frame>();
    Form auxiliaryHost;
    FileStream profileLock;
    System.Windows.Forms.Timer foregroundTimer;
    bool initializing, interference;
    string error = "loading";
    string currentAccountName;
    string groupScript;
    string lookupScript;
    string copilotScript, copilotPath, copilotDigest;
    string scriptPath, formattingPath, nativePath, lookupPath;
    string scriptDigest, formattingDigest, nativeDigest, lookupDigest;

    sealed class CrossChatContext
    {
        public CoreWebView2 Core;
        public string PrimarySnapshot;
        public bool ProtectPrimary;
    }

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
            string nativeDirectory = Path.GetDirectoryName(scriptPath) ?? "";
            formattingPath = Path.Combine(nativeDirectory, "formatting.js");
            lookupPath = Path.Combine(nativeDirectory, "lookup.js");
            copilotPath = Path.Combine(nativeDirectory, "copilot.js");
            nativeDigest = FileDigest(nativePath);
            RefreshScripts();
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
        return await Inspect(controller.CoreWebView2);
    }
    async Task<Dictionary<string, object>> Inspect(CoreWebView2 core)
    {
        string surface = Surface.Replace("__EXPECTED_ACCOUNT__", Json.Serialize(expectedAccount));
        string raw = await core.ExecuteScriptAsync(surface);
        return Json.Deserialize<Dictionary<string, object>>(raw);
    }
    bool HostVisible()
    {
        return host.Visible || (auxiliaryHost != null && auxiliaryHost.Visible) ||
               (copilotHost != null && copilotHost.Visible);
    }
    async Task<CoreWebView2> EnsureAuxiliary()
    {
        if (auxiliaryController == null)
        {
            if (environment == null || error == "browser_failed")
                throw new InvalidOperationException("host_unavailable");
            auxiliaryHost = new Form { ShowInTaskbar = false, ClientSize = new Size(1200, 900) };
            var handle = auxiliaryHost.Handle;
            auxiliaryController = await environment.CreateCoreWebView2ControllerAsync(handle);
            auxiliaryController.Bounds = new Rectangle(0, 0, 1200, 900);
            auxiliaryController.IsVisible = true;
            var core = auxiliaryController.CoreWebView2;
            core.FrameCreated += (s, e) => TrackAuxiliaryFrame(e.Frame);
            core.Settings.IsWebMessageEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.AreDefaultScriptDialogsEnabled = false;
            core.NewWindowRequested += (s, e) => { e.Handled = true; };
            core.LaunchingExternalUriScheme += (s, e) => { e.Cancel = true; };
            core.PermissionRequested += (s, e) =>
            {
                e.State = CoreWebView2PermissionState.Deny;
                e.Handled = true;
            };
            core.BasicAuthenticationRequested += (s, e) => { e.Cancel = true; };
            core.ProcessFailed += (s, e) => { error = "browser_failed"; };
            core.Navigate("https://teams.cloud.microsoft/");
        }
        if (HostVisible() || OwnedForeground())
        {
            interference = true;
            throw new InvalidOperationException("foreground_interference");
        }
        return auxiliaryController.CoreWebView2;
    }
    void TrackAuxiliaryFrame(CoreWebView2Frame frame)
    {
        TrackFrame(frame, auxiliaryFrames);
    }
    void TrackFrame(CoreWebView2Frame frame, List<CoreWebView2Frame> frames)
    {
        if (frames.Count >= 100) return;
        frames.Add(frame);
        frame.FrameCreated += (s, e) => TrackFrame(e.Frame, frames);
        frame.Destroyed += (s, e) => frames.Remove(frame);
    }
    async Task<CoreWebView2Frame> CopilotFrame()
    {
        if (copilotController == null)
        {
            copilotHost = new Form { ShowInTaskbar = false, ClientSize = new Size(1200, 900) };
            copilotController = await environment.CreateCoreWebView2ControllerAsync(copilotHost.Handle);
            copilotController.Bounds = new Rectangle(0, 0, 1200, 900);
            copilotController.IsVisible = true;
            var created = copilotController.CoreWebView2;
            created.Settings.IsWebMessageEnabled = false;
            created.Settings.AreHostObjectsAllowed = false;
            created.Settings.AreDefaultScriptDialogsEnabled = false;
            created.FrameCreated += (s, e) => TrackFrame(e.Frame, copilotFrames);
            created.NewWindowRequested += (s, e) => { e.Handled = true; };
            created.LaunchingExternalUriScheme += (s, e) => { e.Cancel = true; };
            created.PermissionRequested += (s, e) => { e.State = CoreWebView2PermissionState.Deny; e.Handled = true; };
            created.BasicAuthenticationRequested += (s, e) => { e.Cancel = true; };
            created.ProcessFailed += (s, e) => { error = "browser_failed"; };
            created.Navigate("https://teams.cloud.microsoft/");
        }
        var core = copilotController.CoreWebView2;
        var deadline = DateTime.UtcNow.AddSeconds(40);
        while (DateTime.UtcNow < deadline)
        {
            if (HostVisible() || OwnedForeground()) throw new InvalidOperationException("foreground_interference");
            if (await AuxiliaryAuthenticated(core)) break;
            await Task.Delay(250);
        }
        if (!await AuxiliaryAuthenticated(core)) throw new InvalidOperationException("identity_failed");
        var opened = await ExecuteLookup(core, new Dictionary<string, object> { {"phase", "open_copilot"} });
        string openState = Convert.ToString(opened["state"]);
        if (openState != "copilot_already_open" && openState != "copilot_open_clicked")
            throw new InvalidOperationException(openState);
        deadline = DateTime.UtcNow.AddSeconds(30);
        while (DateTime.UtcNow < deadline)
        {
            CoreWebView2Frame selected = null;
            foreach (var frame in copilotFrames.ToArray())
            {
                if (DateTime.UtcNow >= deadline) break;
                if (frame.IsDestroyed() != 0) continue;
                var value = await ExecuteCopilot(frame, new Dictionary<string, object> { {"phase", "status"} });
                string state = Convert.ToString(value["state"]);
                if (state == "ready" || state == "not_ready")
                {
                    if (selected != null) throw new InvalidOperationException("copilot_frame_ambiguous");
                    selected = frame;
                }
            }
            if (selected != null) return selected;
            await Task.Delay(250);
        }
        throw new TimeoutException("copilot_not_ready");
    }
    async Task<Dictionary<string, object>> ExecuteCopilot(CoreWebView2Frame frame, Dictionary<string, object> args)
    {
        if (!ScriptsUnchanged()) throw new InvalidOperationException("script_changed_during_operation");
        if (HostVisible() || OwnedForeground()) throw new InvalidOperationException("foreground_interference");
        var execution = frame.ExecuteScriptAsync("(" + copilotScript + ")(" + Json.Serialize(args) + ")");
        if (await Task.WhenAny(execution, Task.Delay(5000)) != execution)
            throw new TimeoutException("copilot_script_timeout");
        var result = Json.Deserialize<Dictionary<string, object>>(await execution);
        if (!ScriptsUnchanged()) throw new InvalidOperationException("script_changed_during_operation");
        if (HostVisible() || OwnedForeground()) throw new InvalidOperationException("foreground_interference");
        if (result == null)
        {
            string phase=Convert.ToString(args["phase"]);
            if (phase == "status" || phase == "inspect")
                return new Dictionary<string, object> { {"state", "frame_loading"} };
            throw new InvalidOperationException("copilot_guard_failed");
        }
        return result;
    }
    async Task<Dictionary<string, object>> CopilotOperation(string operation, Dictionary<string, object> args)
    {
        var status = await Status();
        if (Convert.ToString(status["state"]) != "authenticated" || Flag(status, "restart_required"))
            throw new InvalidOperationException("identity_failed");
        string snapshot = await PrimarySnapshot();
        var frame = await CopilotFrame();
        if (snapshot != await PrimarySnapshot())
        {
            if (operation == "teams_copilot_status")
                return new Dictionary<string, object> { {"state", "not_ready"},
                    {"work_iq_enabled", false}, {"composer_nonempty", true} };
            throw new InvalidOperationException("primary_state_changed");
        }
        if (operation == "teams_copilot_status")
            return await ExecuteCopilot(frame, new Dictionary<string, object> { {"phase", "status"} });
        if (operation == "teams_copilot_models")
        {
            object selectedModel;
            if (args.TryGetValue("model", out selectedModel))
            {
                if (!(selectedModel is string) || ((string)selectedModel).Length < 1 ||
                    ((string)selectedModel).Length > 120) throw new InvalidDataException();
                await CopilotModelOperation(frame, "select_model", (string)selectedModel);
            }
            var catalog = await CopilotModelOperation(frame, "models", null);
            if (snapshot != await PrimarySnapshot()) throw new InvalidOperationException("primary_state_changed");
            return catalog;
        }
        object rawId;
        if (!args.TryGetValue("request_id", out rawId) || !(rawId is string) ||
            !Regex.IsMatch((string)rawId, "^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")) throw new InvalidDataException();
        var request = new Dictionary<string, object> { {"requestId", rawId} };
        if (operation == "teams_copilot_ask")
        {
            object model;
            if (args.TryGetValue("model", out model) && model != null)
            {
                if (!(model is string) || ((string)model).Length < 1 || ((string)model).Length > 120)
                    throw new InvalidDataException();
                await CopilotModelOperation(frame, "select_model", (string)model);
            }
            object question;
            if (!args.TryGetValue("question", out question) || !(question is string) ||
                ((string)question).Length < 1 || ((string)question).Length > 2000) throw new InvalidDataException();
            request["question"] = question;
            request["phase"] = "prepare";
            var prepared = await ExecuteCopilot(frame, request);
            var inputDeadline = DateTime.UtcNow.AddSeconds(2);
            while (Convert.ToString(prepared["state"]) == "input_unverified" &&
                   DateTime.UtcNow < inputDeadline)
            {
                await Task.Delay(100);
                request["phase"] = "verify_prepare";
                prepared = await ExecuteCopilot(frame, request);
            }
            if (Convert.ToString(prepared["state"]) != "prepared")
                throw new InvalidOperationException(Convert.ToString(prepared["state"]));
            if (snapshot != await PrimarySnapshot()) throw new InvalidOperationException("primary_state_changed");
            request["phase"] = "submit";
        }
        else request["phase"] = "read";
        var result = await ExecuteCopilot(frame, request);
        if (snapshot != await PrimarySnapshot()) throw new InvalidOperationException("primary_state_changed");
        return result;
    }
    async Task<Dictionary<string, object>> CopilotModelOperation(CoreWebView2Frame frame, string phase, string model)
    {
        var deadline=DateTime.UtcNow.AddSeconds(8);
        while(DateTime.UtcNow<deadline)
        {
            var value=await ExecuteCopilot(frame,new Dictionary<string,object>{{"phase",phase},{"model",model}});
            string state=Convert.ToString(value["state"]);
            if(state=="models" || state=="model_selected") return value;
            if(state!="model_menu_opening" && state!="model_selection_pending")
                throw new InvalidOperationException(state);
            await Task.Delay(150);
        }
        throw new TimeoutException("model_selector_timeout");
    }
    async Task<List<object>> InspectAuxiliaryFrames()
    {
        var result = new List<object>();
        var tracked = new List<CoreWebView2Frame>(auxiliaryFrames);
        tracked.AddRange(copilotFrames);
        int inspected = 0;
        foreach (var frame in tracked.ToArray())
        {
            if (inspected++ >= 8) break;
            if (frame.IsDestroyed() != 0) continue;
            try {
                var value = await ExecuteCopilot(frame, new Dictionary<string, object> { {"phase", "inspect"} });
                if (Convert.ToString(value["state"]) != "unsupported_surface") {
                    value["frame_id"] = frame.FrameId;
                    result.Add(value);
                }
            } catch (COMException) {
                result.Add(new Dictionary<string, object> { {"state", "frame_unavailable"} });
            } catch (TimeoutException) {
                result.Add(new Dictionary<string, object> { {"state", "frame_timeout"} });
            }
        }
        return result;
    }
    async Task<bool> AuxiliaryAuthenticated(CoreWebView2 core)
    {
        var page = await Inspect(core);
        if (Flag(page, "blocked") || Flag(page, "login"))
            return false;
        if (!Flag(page, "app"))
            return false;
        await core.ExecuteScriptAsync(
            "(() => { if(!document.querySelector('[data-tid=me-control-menu-dialog]')) " +
            "document.querySelector('[data-tid=me-control-avatar-trigger]')?.click(); })()");
        page = await Inspect(core);
        bool authenticated = Flag(page, "account_verified") &&
                            !String.IsNullOrWhiteSpace(Convert.ToString(page["account_name"]));
        if (authenticated)
            currentAccountName = Convert.ToString(page["account_name"]);
        await core.ExecuteScriptAsync(
            "(() => { if(document.querySelector('[data-tid=me-control-menu-dialog]')) " +
            "document.querySelector('[data-tid=me-control-avatar-trigger]')?.click(); })()");
        return authenticated;
    }
    async Task<bool> AuxiliaryHasExactChat(CoreWebView2 core, string chatId, string title)
    {
        string script = @"(() => {
          const titleNode=document.querySelector('[data-tid=chat-title]');
          const headerId=titleNode?.closest('[id^=chat-header-]')?.id||'';
          return {host:location.hostname, app:!!document.querySelector('[data-tid=experience-layout]'),
            title:titleNode?.textContent.trim()||'', headerId};
        })()";
        string raw = await core.ExecuteScriptAsync(script);
        var page = Json.Deserialize<Dictionary<string, object>>(raw);
        return page != null &&
               (Convert.ToString(page["host"]) == "teams.cloud.microsoft" ||
                Convert.ToString(page["host"]) == "teams.microsoft.com") &&
               Flag(page, "app") && Convert.ToString(page["title"]) == title &&
               Convert.ToString(page["headerId"]) == "chat-header-" + chatId;
    }
    async Task OpenExactChatOnAuxiliary(CoreWebView2 core, string chatId, string title)
    {
        if (auxiliaryController == null)
            return;
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120)
            throw new InvalidDataException();
        if (await AuxiliaryHasExactChat(core, chatId, title))
            return;
        if (!chatId.StartsWith("19:", StringComparison.Ordinal))
            return;

        var snapshot = await ExecuteChat(core, new Dictionary<string, object> {
            { "phase", "snapshot" }
        });
        if (Flag(snapshot, "dirty"))
            throw new InvalidOperationException("draft_exists");

        string deepLink = "https://teams.microsoft.com/l/chat/" +
                          Uri.EscapeDataString(chatId) + "/conversations";
        core.Navigate(deepLink);
        var deadline = DateTime.UtcNow.AddSeconds(25);
        while (DateTime.UtcNow < deadline)
        {
            if (HostVisible() || OwnedForeground())
            {
                interference = true;
                throw new InvalidOperationException("foreground_interference");
            }
            var page = await Inspect(core);
            if (Flag(page, "blocked") || Flag(page, "login"))
                throw new InvalidOperationException("identity_failed");
            if (Flag(page, "app"))
            {
                if (!await AuxiliaryAuthenticated(core))
                    throw new InvalidOperationException("identity_failed");
                if (await AuxiliaryHasExactChat(core, chatId, title))
                    return;
            }
            await Task.Delay(200);
        }
        throw new InvalidOperationException("destination_failed");
    }
    async Task<string> PrimarySnapshot()
    {
        const string snapshot = @"(() => {
          const mix = value => {
            let a=2166136261, b=2246822519;
            for (let i=0;i<value.length;i++) {
              const c=value.charCodeAt(i); a=Math.imul(a^c,16777619); b=Math.imul(b+c,3266489917);
            }
            return [a>>>0,b>>>0,value.length].join(':');
          };
          const editors=Array.from(document.querySelectorAll('[data-tid=ckeditor][contenteditable=true]'));
          const hasDraft=e => {
            const text=(e.innerText||'').replace(/\n/g,'').trim().length>0;
            const rich=!!e.querySelector('img,video,table,iframe,object,a') ||
              e.querySelectorAll('p').length>1 || e.querySelectorAll('br').length>1;
            let root=e;
            const send='button[data-tid=""newMessageCommands-send""],button[data-tid=""sendMessageCommands-send""]';
            for(let i=0;root&&i<12;i++){
              if(root===document.body||root===document.documentElement){root=null;break;}
              if(root.querySelector(send))break;
              root=root.parentElement;
            }
            const files=Array.from(document.querySelectorAll('input[type=file]'))
              .some(input=>input.files?.length);
            if(!root)return text||rich||files;
            const clone=root.cloneNode(true);
            clone.querySelectorAll('[data-tid=""ckeditor""],[contenteditable=true],button,[role=button],input')
              .forEach(element=>element.remove());
            const attachmentText=(clone.innerText||clone.textContent||'').replace(/\s+/g,' ').trim();
            const namedFile=/(?:^|\s)\S{1,180}\.[A-Za-z0-9]{1,16}(?:$|\s)/i
              .test(attachmentText);
            const uploading=!!root.querySelector('[role=progressbar],[aria-busy=true],[data-tid*=upload-progress],[data-tid*=file-progress]');
            return text||rich||files||namedFile||uploading;
          };
          const title=document.querySelector('[data-tid=chat-title]');
          return JSON.stringify({href:location.href, title:title?.textContent.trim()||'',
            headerId:title?.closest('[id^=chat-header-]')?.id||'',
            dirty:editors.some(hasDraft),
            editors:editors.map(e=>({id:e.id,text:mix(e.innerText||''),html:mix(e.innerHTML),
              active:document.activeElement===e}))});
        })()";
        string raw = await controller.CoreWebView2.ExecuteScriptAsync(snapshot);
        return Json.Deserialize<string>(raw);
    }
    async Task<Dictionary<string, object>> FindChats(Dictionary<string, object> args)
    {
        object rawQuery, rawLimit;
        if (!args.TryGetValue("query", out rawQuery) || !args.TryGetValue("limit", out rawLimit))
            throw new InvalidDataException();
        string query = rawQuery as string;
        int limit = Convert.ToInt32(rawLimit);
        if (String.IsNullOrWhiteSpace(query) || query.Length > 120 || limit < 1 || limit > 50)
            throw new InvalidDataException();

        var context = await BeginCrossChat(true);
        string authorName = currentAccountName;
        var prepared = await ExecuteLookup(context.Core,
            new Dictionary<string, object> { { "phase", "prepare_search" } });
        object inputValue;
        var input = prepared.TryGetValue("input", out inputValue)
                        ? inputValue as Dictionary<string, object>
                        : null;
        if (Convert.ToString(prepared["state"]) != "search_prepared" || input == null ||
            Convert.ToString(input["id"]) != "ms-searchux-input" ||
            Convert.ToString(input["tid"]) != "AUTOSUGGEST_INPUT" ||
            Convert.ToString(input["role"]) != "combobox")
            throw new InvalidOperationException("search_control_unavailable");
        if (!ScriptsUnchanged() || !await PrimaryUnchanged(context))
            throw new InvalidOperationException("primary_state_changed");
        await context.Core.CallDevToolsProtocolMethodAsync(
            "Input.insertText", Json.Serialize(new Dictionary<string, object> { { "text", query } }));
        if (!ScriptsUnchanged())
            throw new InvalidOperationException("script_changed_during_operation");

        var deadline = DateTime.UtcNow.AddSeconds(20);
        Dictionary<string, object> search = null;
        string state = "waiting";
        while (DateTime.UtcNow < deadline)
        {
            if (!await PrimaryUnchanged(context))
                throw new InvalidOperationException("primary_state_changed");
            search = await ExecuteLookup(context.Core, new Dictionary<string, object> {
                { "phase", "search_results" }, { "query", query }, { "limit", limit }
            });
            state = Convert.ToString(search["state"]);
            if (state != "waiting")
                break;
            await Task.Delay(200);
        }
        if (state == "waiting")
            throw new TimeoutException("lookup_timeout");
        if (state == "ambiguous")
            throw new InvalidOperationException("lookup_ambiguous_use_email");
        if (state == "empty")
        {
            object truncatedValue;
            bool emptyTruncated = search.TryGetValue("truncated", out truncatedValue) &&
                                  truncatedValue is bool && (bool)truncatedValue;
            if (emptyTruncated)
                throw new InvalidOperationException("lookup_ambiguous_use_email");
            await VerifyCrossChat(context, authorName);
            return new Dictionary<string, object> {
                { "state", "found" }, { "scope", "teams_search" },
                { "items", new object[0] }, { "truncated", false }
            };
        }
        if (state != "search_results")
            throw new InvalidOperationException("lookup_results_unverified");

        object rawCandidates, rawTruncated;
        var candidates = search.TryGetValue("candidates", out rawCandidates)
                            ? rawCandidates as System.Collections.IList
                            : null;
        if (candidates == null || !search.TryGetValue("truncated", out rawTruncated) ||
            !(rawTruncated is bool) || candidates.Count > 100)
            throw new InvalidOperationException("lookup_results_unverified");
        bool truncated = (bool)rawTruncated;
        var exactPeople = new List<Dictionary<string, object>>();
        var exactGroups = new List<Dictionary<string, object>>();
        var seenKeys = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in candidates)
        {
            var candidate = item as Dictionary<string, object>;
            if (candidate == null || !candidate.ContainsKey("key") || !candidate.ContainsKey("type") ||
                !candidate.ContainsKey("title") || !candidate.ContainsKey("exact_match") ||
                !candidate.ContainsKey("match_kind"))
                throw new InvalidOperationException("lookup_results_unverified");
            string key = Convert.ToString(candidate["key"]),
                   type = Convert.ToString(candidate["type"]),
                   title = Convert.ToString(candidate["title"]),
                   matchKind = Convert.ToString(candidate["match_kind"]);
            if (String.IsNullOrWhiteSpace(key) || key.Length > 256 ||
                String.IsNullOrWhiteSpace(title) || title.Length > 120 ||
                (type != "person" && type != "group") || !seenKeys.Add(key) ||
                !(candidate["exact_match"] is bool) ||
                (matchKind != "title" && matchKind != "email" && matchKind != ""))
                throw new InvalidOperationException("lookup_results_unverified");
            if ((bool)candidate["exact_match"])
            {
                if (type == "person") exactPeople.Add(candidate);
                else exactGroups.Add(candidate);
            }
        }
        if (truncated)
            throw new InvalidOperationException("lookup_ambiguous_use_email");
        Dictionary<string, object> selected = null;
        if (exactPeople.Count == 1)
            selected = exactPeople[0];
        else if (exactPeople.Count > 1)
            throw new InvalidOperationException("lookup_ambiguous_use_email");
        else if (exactGroups.Count == 1)
            selected = exactGroups[0];
        else if (exactGroups.Count > 1)
            throw new InvalidOperationException("lookup_ambiguous_use_email");
        if (selected == null)
        {
            await VerifyCrossChat(context, authorName);
            return new Dictionary<string, object> {
                { "state", "found" }, { "scope", "teams_search" },
                { "items", new object[0] }, { "truncated", false }
            };
        }

        string selectedKey = Convert.ToString(selected["key"]);
        string selectedType = Convert.ToString(selected["type"]);
        string selectedTitle = Convert.ToString(selected["title"]);
        var selectRequest = new Dictionary<string, object> {
            { "phase", "select_result" }, { "query", query }, { "key", selectedKey },
            { "type", selectedType }, { "title", selectedTitle }
        };
        var selectedResult = await ExecuteLookup(context.Core, selectRequest);
        if (Convert.ToString(selectedResult["state"]) != "result_selected")
            throw new InvalidOperationException("lookup_selection_unverified");

        var resolveRequest = new Dictionary<string, object> {
            { "phase", "resolved_chat" }, { "query", query }, { "key", selectedKey },
            { "type", selectedType }, { "title", selectedTitle }
        };
        deadline = DateTime.UtcNow.AddSeconds(20);
        Dictionary<string, object> resolved = null;
        state = "waiting";
        while (DateTime.UtcNow < deadline)
        {
            if (!await PrimaryUnchanged(context))
                throw new InvalidOperationException("primary_state_changed");
            resolved = await ExecuteLookup(context.Core, resolveRequest);
            state = Convert.ToString(resolved["state"]);
            if (state != "waiting")
                break;
            await Task.Delay(200);
        }
        if (state != "chat_resolved")
            throw new InvalidOperationException(state == "waiting" ? "lookup_timeout" :
                                                 "lookup_resolution_unverified");
        object rawChat;
        var chat = resolved.TryGetValue("chat", out rawChat)
                       ? rawChat as Dictionary<string, object>
                       : null;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidOperationException("lookup_resolution_unverified");
        string chatId = Convert.ToString(chat["chat_id"]),
               chatTitle = Convert.ToString(chat["title"]);
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(chatTitle) || chatTitle.Length > 120 ||
            chatTitle != selectedTitle)
            throw new InvalidOperationException("lookup_resolution_unverified");
        await VerifyCrossChat(context, authorName);
        return new Dictionary<string, object> {
            { "state", "found" }, { "scope", "teams_search" },
            { "items", new object[] { new Dictionary<string, object> {
                { "chat_id", chatId }, { "title", chatTitle }
            } } }, { "truncated", false }
        };
    }
    async Task<Dictionary<string, object>> FindChatsDiagnostics(Dictionary<string, object> args)
    {
        object requested;
        if (!args.TryGetValue("diagnostics", out requested) || !(requested is bool) ||
            !(bool)requested || String.IsNullOrEmpty(lookupScript))
            throw new InvalidDataException();
        object rawQuery;
        string query = args.TryGetValue("query", out rawQuery) ? rawQuery as string : null;
        if (rawQuery != null && (query == null || String.IsNullOrWhiteSpace(query) || query.Length > 120))
            throw new InvalidDataException();
        object rawSnapshot;
        bool snapshotPrimary = args.TryGetValue("snapshot_primary", out rawSnapshot) &&
                               rawSnapshot is bool && (bool)rawSnapshot;
        if (rawSnapshot != null && !(rawSnapshot is bool))
            throw new InvalidDataException();
        object rawOpenCopilot;
        bool openCopilot = args.TryGetValue("open_copilot", out rawOpenCopilot) &&
                           rawOpenCopilot is bool && (bool)rawOpenCopilot;
        if (rawOpenCopilot != null && !(rawOpenCopilot is bool))
            throw new InvalidDataException();
        object rawInspectCopilot;
        bool inspectCopilot = args.TryGetValue("inspect_copilot", out rawInspectCopilot) &&
                              rawInspectCopilot is bool && (bool)rawInspectCopilot;
        if (rawInspectCopilot != null && !(rawInspectCopilot is bool))
            throw new InvalidDataException();
        if ((openCopilot || inspectCopilot) && (openCopilot == inspectCopilot || query != null))
            throw new InvalidDataException();
        var context = await BeginCrossChat(true, !(openCopilot || inspectCopilot));
        string authorName = currentAccountName;
        var primaryMetadata = snapshotPrimary ? SnapshotMetadata(context.PrimarySnapshot) : null;
        if (query == null && snapshotPrimary)
        {
            await VerifyCrossChat(context, authorName);
            return new Dictionary<string, object> {
                { "state", "diagnostics" }, { "primary_snapshot", primaryMetadata }
            };
        }
        if (query != null)
        {
            var prepared = await ExecuteLookup(context.Core,
                new Dictionary<string, object> { { "phase", "prepare_search" } });
            object inputValue;
            var input = prepared.TryGetValue("input", out inputValue)
                            ? inputValue as Dictionary<string, object>
                            : null;
            if (Convert.ToString(prepared["state"]) != "search_prepared" || input == null ||
                Convert.ToString(input["id"]) != "ms-searchux-input" ||
                Convert.ToString(input["tid"]) != "AUTOSUGGEST_INPUT" ||
                Convert.ToString(input["role"]) != "combobox")
                throw new InvalidOperationException("search_control_unavailable");
            if (!ScriptsUnchanged())
                throw new InvalidOperationException("script_changed_during_operation");
            await context.Core.CallDevToolsProtocolMethodAsync(
                "Input.insertText", Json.Serialize(new Dictionary<string, object> { { "text", query } }));
            if (!ScriptsUnchanged())
                throw new InvalidOperationException("script_changed_during_operation");
            await Task.Delay(1500);
        }
        var result = await ExecuteLookup(context.Core,
            new Dictionary<string, object> { { "phase", openCopilot ? "open_copilot" : "diagnostics" } });
        object resultState;
        var allowedOpenStates = new HashSet<string> { "copilot_open_clicked", "copilot_already_open",
            "copilot_control_ambiguous", "copilot_control_disabled", "draft_exists" };
        if (result == null || !result.TryGetValue("state", out resultState) ||
            (openCopilot ? !allowedOpenStates.Contains(Convert.ToString(resultState)) :
             Convert.ToString(resultState) != "diagnostics"))
            throw new InvalidOperationException("host_operation_failed");
        await VerifyCrossChat(context, authorName);
        if (inspectCopilot)
        {
            result["embedded_surfaces"] = await InspectAuxiliaryFrames();
        }
        if (snapshotPrimary)
            result["primary_snapshot"] = primaryMetadata;
        return result;
    }
    static bool Flag(Dictionary<string, object> value, string name)
    {
        object x;
        return value.TryGetValue(name, out x) && x is bool && (bool)x;
    }
    static bool InsidePoint(Dictionary<string, object> value, double x, double y)
    {
        object raw;
        var rect = value.TryGetValue("trigger_rect", out raw)
                       ? raw as Dictionary<string, object>
                       : null;
        if (rect == null || !rect.ContainsKey("x") || !rect.ContainsKey("y") ||
            !rect.ContainsKey("width") || !rect.ContainsKey("height"))
            return false;
        double left = Convert.ToDouble(rect["x"]), top = Convert.ToDouble(rect["y"]),
               width = Convert.ToDouble(rect["width"]), height = Convert.ToDouble(rect["height"]);
        return width > 0 && height > 0 && x >= left && x <= left + width &&
               y >= top && y <= top + height;
    }
    static string FileDigest(string path)
    {
        if (String.IsNullOrEmpty(path) || !File.Exists(path))
            return null;
        using (var sha = SHA256.Create()) using (
            var file = File.OpenRead(path)) return BitConverter.ToString(sha.ComputeHash(file));
    }
    static string ReadScriptSnapshot(string path, out string digest)
    {
        byte[] bytes = File.ReadAllBytes(path);
        using (var sha = SHA256.Create())
            digest = BitConverter.ToString(sha.ComputeHash(bytes));
        return Encoding.UTF8.GetString(bytes);
    }
    void RefreshScripts()
    {
        string chatDigest, formatDigest, lookupCurrentDigest, copilotCurrentDigest;
        string chat = ReadScriptSnapshot(scriptPath, out chatDigest);
        string formatting = ReadScriptSnapshot(formattingPath, out formatDigest);
        string lookup = ReadScriptSnapshot(lookupPath, out lookupCurrentDigest);
        string copilot = ReadScriptSnapshot(copilotPath, out copilotCurrentDigest);
        if (String.IsNullOrWhiteSpace(chat) || String.IsNullOrWhiteSpace(formatting) ||
            String.IsNullOrWhiteSpace(lookup) || chatDigest != FileDigest(scriptPath) ||
            formatDigest != FileDigest(formattingPath) || lookupCurrentDigest != FileDigest(lookupPath) ||
            copilotCurrentDigest != FileDigest(copilotPath))
            throw new IOException("The Teams JavaScript bundle changed while it was loading.");
        groupScript = "(request) => (" + chat + ")(request, " + formatting + ")";
        lookupScript = lookup;
        copilotScript = copilot;
        scriptDigest = chatDigest;
        formattingDigest = formatDigest;
        lookupDigest = lookupCurrentDigest;
        copilotDigest = copilotCurrentDigest;
    }
    bool ScriptsUnchanged()
    {
        return scriptDigest != null && formattingDigest != null && lookupDigest != null &&
               scriptDigest == FileDigest(scriptPath) &&
               formattingDigest == FileDigest(formattingPath) &&
               lookupDigest == FileDigest(lookupPath) && copilotDigest == FileDigest(copilotPath);
    }
    async Task<Dictionary<string, object>> ExecuteChat(
        CoreWebView2 core, Dictionary<string, object> request)
    {
        if (!ScriptsUnchanged())
            throw new InvalidOperationException("script_changed_during_operation");
        string script = "(() => {try { return (" + groupScript + ")( " + Json.Serialize(request) +
            "); } catch(error) { return {state:'script_error',error_name:error.name,error_message:String(error.message).slice(0,200)}; }})()";
        var result = Json.Deserialize<Dictionary<string, object>>(
            await core.ExecuteScriptAsync(script));
        if (!ScriptsUnchanged())
            throw new InvalidOperationException("script_changed_during_operation");
        if (result == null)
            throw new InvalidOperationException("The browser script returned no result.");
        return result;
    }
    async Task<Dictionary<string, object>> ExecuteLookup(
        CoreWebView2 core, Dictionary<string, object> request)
    {
        if (!ScriptsUnchanged())
            throw new InvalidOperationException("script_changed_during_operation");
        var result = Json.Deserialize<Dictionary<string, object>>(
            await core.ExecuteScriptAsync("(" + lookupScript + ")( " + Json.Serialize(request) + ")"));
        if (!ScriptsUnchanged())
            throw new InvalidOperationException("script_changed_during_operation");
        if (result == null)
            throw new InvalidOperationException("The lookup script returned no result.");
        return result;
    }
    async Task EnsureChatWorkspace(CoreWebView2 core)
    {
        string navigationId = Guid.NewGuid().ToString("N");
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (DateTime.UtcNow < deadline)
        {
            var result = await ExecuteChat(core, new Dictionary<string, object> {
                { "phase", "workspace_ready" }, { "navigationId", navigationId }
            });
            string state = Convert.ToString(result["state"]);
            if (state == "workspace_ready")
                return;
            if (state != "waiting" && state != "workspace_opening")
                throw new InvalidOperationException("workspace_unavailable");
            await Task.Delay(200);
        }
        throw new TimeoutException();
    }
    static bool SnapshotIsDirty(string snapshot)
    {
        var value = Json.Deserialize<Dictionary<string, object>>(snapshot);
        return Flag(value, "dirty");
    }
    Dictionary<string, object> SnapshotMetadata(string snapshot)
    {
        var value = Json.Deserialize<Dictionary<string, object>>(snapshot);
        object rawEditors;
        var editors = value.TryGetValue("editors", out rawEditors)
                          ? rawEditors as System.Collections.IList
                          : null;
        string fingerprint;
        using (var sha = SHA256.Create())
            fingerprint = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(snapshot)))
                             .Replace("-", "")
                             .ToLowerInvariant();
        return new Dictionary<string, object> {
            { "fingerprint", fingerprint }, { "editor_count", editors == null ? 0 : editors.Count },
            { "dirty", Flag(value, "dirty") }
        };
    }
    async Task<CrossChatContext> BeginCrossChat(bool forceAuxiliary = false,
                                                bool ensureAuxiliaryWorkspace = true)
    {
        var status = await Status();
        if (Convert.ToString(status["state"]) != "authenticated" || interference ||
            Flag(status, "restart_required") || String.IsNullOrWhiteSpace(currentAccountName))
            throw new InvalidOperationException("identity_failed");
        string author = currentAccountName;
        await EnsureChatWorkspace(controller.CoreWebView2);
        string snapshot = await PrimarySnapshot();
        bool protect = SnapshotIsDirty(snapshot);
        CoreWebView2 core = controller.CoreWebView2;
        if (protect || forceAuxiliary)
        {
            core = await EnsureAuxiliary();
            var deadline = DateTime.UtcNow.AddSeconds(25);
            bool authenticated = false;
            while (DateTime.UtcNow < deadline)
            {
                if (HostVisible() || OwnedForeground())
                {
                    interference = true;
                    throw new InvalidOperationException("foreground_interference");
                }
                authenticated = await AuxiliaryAuthenticated(core);
                if (authenticated)
                    break;
                await Task.Delay(200);
            }
            if (!authenticated || currentAccountName != author)
                throw new InvalidOperationException("identity_failed");
            if (ensureAuxiliaryWorkspace)
                await EnsureChatWorkspace(core);
        }
        protect = protect || forceAuxiliary;
        var context = new CrossChatContext {
            Core = core,
            PrimarySnapshot = snapshot,
            ProtectPrimary = protect
        };
        if (protect && !await PrimaryUnchanged(context))
            throw new InvalidOperationException("primary_state_changed");
        return context;
    }
    async Task<bool> PrimaryUnchanged(CrossChatContext context)
    {
        if (HostVisible() || OwnedForeground())
        {
            interference = true;
            throw new InvalidOperationException("foreground_interference");
        }
        if (!context.ProtectPrimary)
            return true;
        return String.Equals(context.PrimarySnapshot, await PrimarySnapshot(),
                             StringComparison.Ordinal);
    }
    async Task VerifyCrossChat(CrossChatContext context, string author)
    {
        if (!await AuxiliaryAuthenticated(context.Core) || currentAccountName != author)
            throw new InvalidOperationException("identity_failed");
        if (!await PrimaryUnchanged(context))
            throw new InvalidOperationException("primary_state_changed");
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
            restartRequired = scriptDigest == null || formattingDigest == null ||
                              nativeDigest == null || lookupDigest == null ||
                              scriptDigest != FileDigest(scriptPath) ||
                              formattingDigest != FileDigest(formattingPath) ||
                              nativeDigest != FileDigest(nativePath) ||
                              lookupDigest != FileDigest(lookupPath);
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
                                                { "capabilities", new string[0] },
                                                { "samples", samples.Count > 0 && !account ? samples : null },
                                                { "reason", reason } };
    }
    async Task<Dictionary<string, object>> Dispatch(Dictionary<string, object> request)
    {
        if (!String.IsNullOrEmpty(scriptPath) && !String.IsNullOrEmpty(formattingPath) &&
            !String.IsNullOrEmpty(lookupPath))
            RefreshScripts();
        object version, operation;
        if (!request.TryGetValue("version", out version) || Convert.ToInt32(version) != 1 ||
            !request.TryGetValue("operation", out operation))
            throw new InvalidDataException();
        string op = Convert.ToString(operation);
        if (op == "teams_web_status")
            return await Status();
        if (op == "teams_web_upload_status")
        {
            object raw;
            if (!request.TryGetValue("arguments", out raw) ||
                !(raw is Dictionary<string, object>))
                throw new InvalidDataException();
            return UploadStatus((Dictionary<string, object>)raw);
        }
        if (op == "teams_web_upload_readiness" || op == "teams_web_upload_files")
        {
            object raw;
            if (!request.TryGetValue("arguments", out raw) ||
                !(raw is Dictionary<string, object>))
                throw new InvalidDataException();
            var args = (Dictionary<string, object>)raw;
            if (op == "teams_web_upload_readiness")
                return await UploadReadiness(args);
            return await UploadFiles(args);
        }
        if (op == "teams_copilot_status" || op == "teams_copilot_models" ||
            op == "teams_copilot_ask" || op == "teams_copilot_read")
        {
            object raw;
            if (!request.TryGetValue("arguments", out raw) || !(raw is Dictionary<string, object>))
                throw new InvalidDataException();
            return await CopilotOperation(op, (Dictionary<string, object>)raw);
        }
        if (op == "teams_web_find_chats")
        {
            object raw;
            if (!request.TryGetValue("arguments", out raw))
                throw new InvalidDataException();
            var args = raw as Dictionary<string, object>;
            if (args == null)
                throw new InvalidDataException();
            object diagnostic;
            if (args.TryGetValue("diagnostics", out diagnostic))
            {
                if (!(diagnostic is bool) || !(bool)diagnostic)
                    throw new InvalidDataException();
                return await FindChatsDiagnostics(args);
            }
            return await FindChats(args);
        }
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
        if (op == "teams_web_message_link" || op == "teams_web_message_reactions" ||
            op == "teams_web_react_message" || op == "teams_web_set_reaction")
            return await MessageAction(op, request["arguments"] as Dictionary<string, object>);
        if (op == "teams_web_list_chats" || op == "teams_web_read_chat" ||
            op == "teams_web_send_chat" || op == "teams_web_inspect_message" ||
            op == "teams_web_inspect_mention_evidence" || op == "teams_web_edit_message")
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
            if (op == "teams_web_inspect_message" || op == "teams_web_inspect_mention_evidence")
                return await InspectMessage(args, op == "teams_web_inspect_mention_evidence");
            if (op == "teams_web_edit_message")
                return await EditMessage(args);
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
        var context = await BeginCrossChat();
        string authorName = currentAccountName;
        string navigationId = Guid.NewGuid().ToString("N");
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (DateTime.UtcNow < deadline)
        {
            if (!await PrimaryUnchanged(context))
                throw new InvalidOperationException("primary_state_changed");
            var request = new Dictionary<string, object> {
                { "phase", "list" }, { "name", name }, { "limit", limit },
                { "navigationId", navigationId }
            };
            var result = await ExecuteChat(context.Core, request);
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
                await VerifyCrossChat(context, authorName);
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
            chatId ?? "", "^19:[A-Za-z0-9._:-]{1,256}@(?:thread\\.(?:v2|skype)|unq\\.gbl\\.spaces)$");
    }
    async Task<Dictionary<string, object>> Read(Dictionary<string, object> args,
                                                CrossChatContext context = null)
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
        bool ownContext = context == null;
        if (ownContext)
            context = await BeginCrossChat();
        string authorName = currentAccountName;
        if (context.ProtectPrimary)
            await OpenExactChatOnAuxiliary(context.Core, chatId, title);
        string phase = "locate", navigationId = Guid.NewGuid().ToString("N");
        var deadline = DateTime.UtcNow.AddSeconds(25);
        while (DateTime.UtcNow < deadline)
        {
            if (!await PrimaryUnchanged(context))
                throw new InvalidOperationException("primary_state_changed");
            var request = new Dictionary<string, object> {
                { "phase", phase }, { "chatId", chatId }, { "title", title },
                { "limit", limit }, { "navigationId", navigationId }
            };
            var result = await ExecuteChat(context.Core, request);
            string state = Convert.ToString(result["state"]);
            if (state == "read")
            {
                if (Convert.ToString(result["chat_id"]) != chatId ||
                    Convert.ToString(result["title"]) != title)
                    throw new InvalidOperationException();
                if (ownContext)
                    await VerifyCrossChat(context, authorName);
                return result;
            }
            if (state == "ready" || state == "navigated") phase = "read";
            else if (state == "navigate") phase = "navigate";
            else if (state != "waiting") throw new InvalidOperationException(state);
            await Task.Delay(150);
        }
        throw new TimeoutException();
    }
    static string Sha256Text(string value)
    {
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value)))
                .Replace("-", "").ToLowerInvariant();
    }
    static string UploadPayloadDigest(List<Dictionary<string, object>> files)
    {
        return Sha256Text(Json.Serialize(files.ConvertAll(file =>
            new Dictionary<string, object> {
                { "name", file["name"] }, { "size_bytes", file["size_bytes"] },
                { "sha256", file["sha256"] }
            })));
    }
    static Dictionary<string, object> UploadChat(Dictionary<string, object> args)
    {
        object raw;
        if (args == null || !args.TryGetValue("chat", out raw))
            throw new InvalidDataException();
        var chat = raw as Dictionary<string, object>;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidDataException();
        string chatId = chat["chat_id"] as string, title = chat["title"] as string;
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120)
            throw new InvalidDataException();
        return chat;
    }
    static List<Dictionary<string, object>> ValidateUploadFiles(object raw)
    {
        var values = raw as System.Collections.IList;
        if (values == null || values.Count < 1 || values.Count > 10)
            throw new InvalidOperationException("attachment_limit_exceeded");
        var result = new List<Dictionary<string, object>>();
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (object value in values)
        {
            var file = value as Dictionary<string, object>;
            object rawPath, rawName, rawSize, rawHash;
            if (file == null || !file.TryGetValue("path", out rawPath) ||
                !file.TryGetValue("name", out rawName) ||
                !file.TryGetValue("size_bytes", out rawSize) ||
                !file.TryGetValue("sha256", out rawHash))
                throw new InvalidDataException();
            string path = rawPath as string, name = rawName as string, digest = rawHash as string;
            long size;
            try { size = Convert.ToInt64(rawSize); }
            catch { throw new InvalidDataException(); }
            if (String.IsNullOrWhiteSpace(path) || path.Length > 32767 ||
                !Path.IsPathRooted(path) || path.StartsWith(@"\\", StringComparison.Ordinal) ||
                path.StartsWith(@"\\?\", StringComparison.Ordinal) ||
                path.StartsWith(@"\\.\", StringComparison.Ordinal) ||
                Path.GetPathRoot(path).Length < 3 ||
                String.IsNullOrEmpty(name) || Path.GetFileName(path) != name ||
                !Regex.IsMatch(name, @"^[^\\/:*?""<>|\x00-\x1f]{1,180}$") ||
                name.EndsWith(".", StringComparison.Ordinal) ||
                name.EndsWith(" ", StringComparison.Ordinal) ||
                !Regex.IsMatch(digest ?? "", "^[0-9a-f]{64}$") ||
                size < 0 || size > 25L * 1024 * 1024 ||
                !names.Add(name))
                throw new InvalidOperationException("attachment_limit_exceeded");
            string baseName = Path.GetFileNameWithoutExtension(name).TrimEnd(' ', '.').ToUpperInvariant();
            if (baseName == "CON" || baseName == "PRN" || baseName == "AUX" || baseName == "NUL" ||
                Regex.IsMatch(baseName, "^(COM|LPT)[1-9]$"))
                throw new InvalidOperationException("attachment_limit_exceeded");
            total += size;
            if (total > 50L * 1024 * 1024)
                throw new InvalidOperationException("attachment_limit_exceeded");
            try
            {
                var attributes = File.GetAttributes(path);
                if ((attributes & FileAttributes.Directory) != 0 ||
                    (attributes & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("attachment_file_changed");
                var info = new FileInfo(path);
                if (info.Length != size)
                    throw new InvalidOperationException("attachment_file_changed");
                string actual;
                using (var sha = SHA256.Create())
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                    actual = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                if (actual != digest || new FileInfo(path).Length != size)
                    throw new InvalidOperationException("attachment_file_changed");
            }
            catch (InvalidOperationException) { throw; }
            catch { throw new InvalidOperationException("attachment_file_changed"); }
            result.Add(new Dictionary<string, object> {
                { "path", Path.GetFullPath(path) }, { "name", name },
                { "size_bytes", size }, { "sha256", digest }
            });
        }
        return result;
    }
    string UploadClaimsDirectory()
    {
        string path = Path.Combine(Path.GetDirectoryName(profile), "teams-web-sends",
                                   "claims", "uploads");
        Directory.CreateDirectory(path);
        return path;
    }
    Dictionary<string, object> UploadStatus(Dictionary<string, object> args)
    {
        var chat = UploadChat(args);
        object rawId;
        if (!args.TryGetValue("request_id", out rawId) || !(rawId is string))
            throw new InvalidDataException();
        string requestId = (string)rawId;
        if (!Regex.IsMatch(requestId, "^[A-Za-z0-9._-]{1,128}$"))
            throw new InvalidDataException();
        string requestDigest = Sha256Text(requestId);
        string destinationDigest = Sha256Text(Convert.ToString(chat["chat_id"]) + "\0" +
                                                Convert.ToString(chat["title"]));
        string claim = Path.Combine(UploadClaimsDirectory(), requestDigest + ".json");
        string chatId = Convert.ToString(chat["chat_id"]), title = Convert.ToString(chat["title"]);
        var unknown = new Dictionary<string, object> {
            { "state", "unknown" }, { "chat_id", chatId }, { "title", title },
            { "request_id", requestId }
        };
        if (!File.Exists(claim)) return unknown;
        try
        {
            var record = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(claim));
            if (record == null || Convert.ToString(record["request_sha256"]) != requestDigest ||
                Convert.ToString(record["destination_sha256"]) != destinationDigest)
                return unknown;
            string receiptPath = claim + ".receipt";
            if (!File.Exists(receiptPath)) return unknown;
            var receipt = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(receiptPath));
            if (receipt == null || Convert.ToString(receipt["state"]) != "uploaded" ||
                Convert.ToString(receipt["chat_id"]) != chatId ||
                Convert.ToString(receipt["title"]) != title)
                return unknown;
            return receipt;
        }
        catch { return unknown; }
    }
    async Task<List<int>> QueryUploadInputNodeIds(CoreWebView2 core)
    {
        await core.CallDevToolsProtocolMethodAsync("DOM.enable", "{}");
        string documentRaw = await core.CallDevToolsProtocolMethodAsync(
            "DOM.getDocument", "{\"depth\":1,\"pierce\":true}");
        var document = Json.Deserialize<Dictionary<string, object>>(documentRaw);
        var root = document == null ? null : document["root"] as Dictionary<string, object>;
        if (root == null || !root.ContainsKey("nodeId"))
            throw new InvalidOperationException("attachment_input_unavailable");
        string queryRaw = await core.CallDevToolsProtocolMethodAsync(
            "DOM.querySelectorAll", Json.Serialize(new Dictionary<string, object> {
                { "nodeId", Convert.ToInt32(root["nodeId"]) }, { "selector", "input[type=file]" }
            }));
        var query = Json.Deserialize<Dictionary<string, object>>(queryRaw);
        var nodes = query == null ? null : query["nodeIds"] as System.Collections.IList;
        if (nodes == null)
            throw new InvalidOperationException("attachment_input_unavailable");
        var result = new List<int>();
        foreach (object node in nodes)
            result.Add(Convert.ToInt32(node));
        return result;
    }
    async Task SetFileChooserInterception(CoreWebView2 core, bool enabled)
    {
        await core.CallDevToolsProtocolMethodAsync("Page.enable", "{}");
        await core.CallDevToolsProtocolMethodAsync("Page.setInterceptFileChooserDialog",
            Json.Serialize(new Dictionary<string, object> { { "enabled", enabled } }));
    }
    async Task<Dictionary<string, object>> UploadReadiness(Dictionary<string, object> args)
    {
        var chat = UploadChat(args);
        var context = await BeginCrossChat();
        string author = currentAccountName;
        await Read(new Dictionary<string, object> {
            { "chat", chat }, { "limit", 1 }
        }, context);
        var result = await RunChat(context.Core, new Dictionary<string, object> {
            { "phase", "upload_readiness" }, { "chatId", chat["chat_id"] },
            { "title", chat["title"] }
        });
        if (Convert.ToString(result["chat_id"]) != Convert.ToString(chat["chat_id"]) ||
            Convert.ToString(result["title"]) != Convert.ToString(chat["title"]))
            throw new InvalidOperationException("identity_failed");
        bool chooserInterception = false;
        try
        {
            await SetFileChooserInterception(context.Core, true);
            await SetFileChooserInterception(context.Core, false);
            chooserInterception = true;
        }
        catch { }
        if (!chooserInterception)
        {
            try { await SetFileChooserInterception(context.Core, false); }
            catch { }
        }
        bool composerEmpty = Flag(result, "composer_empty");
        bool directInput = Flag(result, "file_input_available");
        bool attachControl = Flag(result, "attach_control_available");
        bool usable = directInput || (attachControl && chooserInterception);
        result["file_chooser_interception_available"] = chooserInterception;
        result["file_input_available"] = usable;
        result["file_input_mode"] = directInput ? "direct" :
            usable ? "attach_menu" : "unavailable";
        if (!composerEmpty)
        {
            result["state"] = "blocked";
            result["reason"] = "composer_has_draft";
        }
        else if (!usable)
        {
            result["state"] = "blocked";
            result["reason"] = "file_input_unavailable";
        }
        else
        {
            result["state"] = "ready";
            result["reason"] = null;
        }
        result["attach_control_available"] = attachControl;
        await VerifyCrossChat(context, author);
        return result;
    }
    async Task<Dictionary<string, object>> UploadFiles(Dictionary<string, object> args)
    {
        var chat = UploadChat(args);
        object rawId, rawFiles;
        if (!args.TryGetValue("request_id", out rawId) || !(rawId is string) ||
            !args.TryGetValue("files", out rawFiles))
            throw new InvalidDataException();
        string requestId = (string)rawId;
        if (!Regex.IsMatch(requestId, "^[A-Za-z0-9._-]{1,128}$"))
            throw new InvalidDataException();
        var files = ValidateUploadFiles(rawFiles);
        string chatId = Convert.ToString(chat["chat_id"]), title = Convert.ToString(chat["title"]);
        string requestDigest = Sha256Text(requestId);
        string destinationDigest = Sha256Text(chatId + "\0" + title);
        string payloadDigest = UploadPayloadDigest(files);
        string claim = Path.Combine(UploadClaimsDirectory(), requestDigest + ".json");
        string receiptPath = claim + ".receipt";
        if (File.Exists(claim))
        {
            try
            {
                var previous = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(claim));
                if (previous == null ||
                    Convert.ToString(previous["request_sha256"]) != requestDigest ||
                    Convert.ToString(previous["destination_sha256"]) != destinationDigest ||
                    Convert.ToString(previous["payload_sha256"]) != payloadDigest)
                    throw new InvalidOperationException("idempotency_conflict");
            }
            catch (InvalidOperationException) { throw; }
            catch { throw new InvalidOperationException("upload_outcome_unknown"); }
            var prior = UploadStatus(args);
            if (Convert.ToString(prior["state"]) == "uploaded")
                return prior;
            throw new InvalidOperationException("upload_outcome_unknown");
        }

        var context = await BeginCrossChat();
        string author = currentAccountName;
        if (context.ProtectPrimary)
            await OpenExactChatOnAuxiliary(context.Core, chatId, title);
        await Read(new Dictionary<string, object> { { "chat", chat }, { "limit", 1 } }, context);
        var request = new Dictionary<string, object> {
            { "phase", "upload_prepare" }, { "chatId", chatId }, { "title", title },
            { "requestId", requestId }, { "authorName", author },
            { "files", files.ConvertAll(file => new Dictionary<string, object> {
                { "name", file["name"] }
            }) }, { "navigationId", Guid.NewGuid().ToString("N") }
        };
        var prepared = await RunChat(context.Core, request);
        if (Convert.ToString(prepared["state"]) != "upload_prepared")
            throw new InvalidOperationException(Convert.ToString(prepared["state"]));

        using (var file = new FileStream(claim, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(
                new Dictionary<string, object> {
                    { "request_sha256", requestDigest },
                    { "destination_sha256", destinationDigest },
                    { "payload_sha256", payloadDigest }
                }));
            file.Write(bytes, 0, bytes.Length);
            file.Flush(true);
        }

        string inputMode = Convert.ToString(prepared["input_mode"]);
        if (inputMode != "direct" && inputMode != "attach_menu")
            throw new InvalidOperationException("attachment_input_unavailable");
        var baselineInputs = await QueryUploadInputNodeIds(context.Core);
        int fileInputNodeId = -1;
        bool fileChooserIntercepted = false;
        Exception inputError = null;
        try
        {
            if (inputMode == "direct")
            {
                if (baselineInputs.Count != 1)
                    throw new InvalidOperationException("attachment_input_unavailable");
                fileInputNodeId = baselineInputs[0];
            }
            else
            {
                await SetFileChooserInterception(context.Core, true);
                fileChooserIntercepted = true;
                request["phase"] = "upload_open_menu";
                var menu = await RunChat(context.Core, request);
                if (Convert.ToString(menu["state"]) != "upload_menu_opened")
                    throw new InvalidOperationException("attachment_input_unavailable");
                request["phase"] = "upload_open_picker";
                var pickerDeadline = DateTime.UtcNow.AddSeconds(8);
                Dictionary<string, object> picker = null;
                while (DateTime.UtcNow < pickerDeadline)
                {
                    picker = await RunChat(context.Core, request);
                    string pickerState = Convert.ToString(picker["state"]);
                    if (pickerState == "upload_picker_opened") break;
                    if (pickerState != "waiting")
                        throw new InvalidOperationException("attachment_input_unavailable");
                    await Task.Delay(150);
                }
                if (picker == null || Convert.ToString(picker["state"]) != "upload_picker_opened")
                    throw new InvalidOperationException("attachment_input_unavailable");
                var inputDeadline = DateTime.UtcNow.AddSeconds(5);
                while (DateTime.UtcNow < inputDeadline)
                {
                    var currentInputs = await QueryUploadInputNodeIds(context.Core);
                    var createdInputs = currentInputs.FindAll(node => !baselineInputs.Contains(node));
                    if (createdInputs.Count == 1)
                    {
                        fileInputNodeId = createdInputs[0];
                        break;
                    }
                    await Task.Delay(100);
                }
                if (fileInputNodeId < 0)
                    throw new InvalidOperationException("attachment_input_unavailable");
            }
            var verifiedFiles = ValidateUploadFiles(rawFiles);
            if (UploadPayloadDigest(verifiedFiles) != payloadDigest)
                throw new InvalidOperationException("attachment_file_changed");
            files = verifiedFiles;
            var paths = files.ConvertAll(file => file["path"]);
            await context.Core.CallDevToolsProtocolMethodAsync("DOM.setFileInputFiles",
                Json.Serialize(new Dictionary<string, object> {
                    { "nodeId", fileInputNodeId }, { "files", paths }
                }));
        }
        catch (Exception error)
        {
            inputError = error;
        }
        if (fileChooserIntercepted)
        {
            try { await SetFileChooserInterception(context.Core, false); }
            catch
            {
                if (inputError == null)
                    inputError = new InvalidOperationException("attachment_input_unavailable");
            }
        }
        if (inputError != null)
            throw inputError;

        request["phase"] = "upload_wait";
        var deadline = DateTime.UtcNow.AddMinutes(3);
        Dictionary<string, object> result = null;
        while (DateTime.UtcNow < deadline)
        {
            if (!await PrimaryUnchanged(context) || !ScriptsUnchanged())
                throw new InvalidOperationException("primary_state_changed");
            result = await RunChat(context.Core, request);
            string state = Convert.ToString(result["state"]);
            if (state == "upload_ready") break;
            if (state != "waiting")
                throw new InvalidOperationException(state);
            await Task.Delay(250);
        }
        if (result == null || Convert.ToString(result["state"]) != "upload_ready")
            throw new InvalidOperationException("attachment_not_ready");

        request["phase"] = "upload_click";
        result = await RunChat(context.Core, request);
        if (Convert.ToString(result["state"]) != "upload_clicked")
            throw new InvalidOperationException("attachment_not_ready");
        request["phase"] = "upload_observe";
        deadline = DateTime.UtcNow.AddMinutes(2);
        while (DateTime.UtcNow < deadline)
        {
            await Task.Delay(250);
            if (!await PrimaryUnchanged(context) || !ScriptsUnchanged())
                throw new InvalidOperationException("primary_state_changed");
            result = await RunChat(context.Core, request);
            string state = Convert.ToString(result["state"]);
            if (state == "uploaded") break;
            if (state != "waiting")
                throw new InvalidOperationException("upload_outcome_unknown");
        }
        if (result == null || Convert.ToString(result["state"]) != "uploaded" ||
            Convert.ToString(result["chat_id"]) != chatId ||
            Convert.ToString(result["title"]) != title)
            throw new InvalidOperationException("upload_outcome_unknown");
        var receiptFiles = result["files"] as System.Collections.IList;
        if (receiptFiles == null || receiptFiles.Count != files.Count)
            throw new InvalidOperationException("upload_outcome_unknown");
        for (int index = 0; index < files.Count; index++)
            if (Convert.ToString(receiptFiles[index]) != Convert.ToString(files[index]["name"]))
                throw new InvalidOperationException("upload_outcome_unknown");
        if (!Regex.IsMatch(Convert.ToString(result["message_id"]),
                           "^message-body-[0-9]{13}$") ||
            String.IsNullOrWhiteSpace(Convert.ToString(result["time"])))
            throw new InvalidOperationException("upload_outcome_unknown");
        await VerifyCrossChat(context, author);
        using (var file = new FileStream(receiptPath, FileMode.CreateNew, FileAccess.Write,
                                         FileShare.None))
        {
            byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(result));
            file.Write(bytes, 0, bytes.Length);
            file.Flush(true);
        }
        return result;
    }
    async Task<Dictionary<string, object>> MessageAction(string operation, Dictionary<string, object> args)
    {
        object rawChat, rawMessage, rawExpected, rawReaction, rawId;
        if (args == null || !args.TryGetValue("chat", out rawChat) ||
            !args.TryGetValue("message_id", out rawMessage)) throw new InvalidDataException();
        var chat = rawChat as Dictionary<string, object>;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidDataException();
        string chatId = chat["chat_id"] as string, title = chat["title"] as string,
               messageId = rawMessage as string;
        string expected = args.TryGetValue("expected_text", out rawExpected) ? rawExpected as string : null;
        string reaction = args.TryGetValue("reaction", out rawReaction) ? rawReaction as string : null;
        string id = args.TryGetValue("request_id", out rawId) ? rawId as string : null;
        bool desiredState = operation == "teams_web_set_reaction";
        bool mutate = operation == "teams_web_react_message" || desiredState;
        object rawSelected, rawEmoji;
        bool selected = !desiredState || (args.TryGetValue("selected", out rawSelected) &&
            rawSelected is bool && (bool)rawSelected);
        string emojiId = args.TryGetValue("emoji_id", out rawEmoji) ? rawEmoji as string : null;
        if (desiredState && (!args.ContainsKey("selected") || !(args["selected"] is bool) ||
            emojiId != "1f440_eyes" || reaction != "Eyes"))
            throw new InvalidDataException();
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120 ||
            !Regex.IsMatch(messageId ?? "", "^message-body-[0-9]{13}$") ||
            (operation != "teams_web_message_link" && (expected == null || expected.Length > 2000)) ||
            (mutate && (String.IsNullOrEmpty(reaction) || reaction.Length > 80 ||
                        !Regex.IsMatch(id ?? "", "^[A-Za-z0-9._-]{1,128}$"))))
            throw new InvalidDataException();
        var context = await BeginCrossChat();
        string author = currentAccountName;
        await Read(new Dictionary<string, object> { { "chat", chat }, { "limit", 1 } }, context);
        var request = new Dictionary<string, object> {
            { "chatId", chatId }, { "title", title }, { "messageId", messageId },
            { "expectedText", expected }, { "reaction", reaction }, { "requestId", id },
            { "selected", selected }, { "emojiId", emojiId }, { "contentOnly", desiredState },
            { "readCurrent", !mutate && Flag(args, "read_current") },
            { "phase", operation == "teams_web_message_link" ? "message_link" : "message_hover" }
        };
        string operationPhase = Convert.ToString(request["phase"]);
        request["phase"] = "message_locate";
        var locateDeadline = DateTime.UtcNow.AddSeconds(15);
        Dictionary<string, object> located;
        do {
            await VerifyCrossChat(context, author);
            located = await RunChat(context.Core, request);
            if (Convert.ToString(located["state"]) != "waiting") break;
            await Task.Delay(250);
        } while (DateTime.UtcNow < locateDeadline);
        if (Convert.ToString(located["state"]) != "message_loaded")
            throw new InvalidOperationException("message_not_loaded");
        request["phase"] = operationPhase;
        var result = await RunChat(context.Core, request);
        if (operation == "teams_web_message_link")
        {
            await VerifyCrossChat(context, author);
            return result;
        }
        if (Convert.ToString(result["state"]) != "hover")
            throw new InvalidOperationException(Convert.ToString(result["state"]));
        var point = result["point"] as Dictionary<string, object>;
        await context.Core.CallDevToolsProtocolMethodAsync("Emulation.setFocusEmulationEnabled",
            "{\"enabled\":true}");
        Exception operationError = null;
        try {
        request["phase"] = "message_open";
        var opened = await RunChat(context.Core, request);
        if (Convert.ToString(opened["state"]) != "toolbar_opening")
            throw new InvalidOperationException("reaction_unavailable");
        await context.Core.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent", Json.Serialize(
            new Dictionary<string, object> { { "type", "mouseMoved" }, { "x", point["x"] },
                { "y", point["y"] } }));
        await Task.Delay(300);
        if (!await PrimaryUnchanged(context) || !ScriptsUnchanged())
            throw new InvalidOperationException("primary_state_changed");
        request["phase"] = "message_reactions";
        result = await RunChat(context.Core, request);
        if (!desiredState || selected) {
            request["phase"] = "message_picker_open";
            var pickerOpened = await RunChat(context.Core, request);
            if (Convert.ToString(pickerOpened["state"]) == "picker_opening") {
                request["phase"] = "message_reactions";
                var pickerDeadline = DateTime.UtcNow.AddSeconds(3);
                do {
                    await Task.Delay(150);
                    result = await RunChat(context.Core, request);
                } while (!Flag(result, "picker_ready") && DateTime.UtcNow < pickerDeadline);
            }
        }
        if (mutate) {
        string claims = Path.Combine(Path.GetDirectoryName(profile), "teams-web-sends", "reactions");
        Directory.CreateDirectory(claims);
        string digest;
        using (var sha = SHA256.Create())
            digest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(id))).Replace("-", "");
        using (var claim = new FileStream(Path.Combine(claims, digest), FileMode.CreateNew,
                                         FileAccess.Write, FileShare.None))
        {
            byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(args));
            claim.Write(bytes, 0, bytes.Length);
            claim.Flush(true);
        }
        request["phase"] = desiredState ? "message_set_reaction" : "message_react";
        result = await RunChat(context.Core, request);
        var deadline = DateTime.UtcNow.AddSeconds(8);
        if (Convert.ToString(result["state"]) == "reaction_clicked")
        {
            request["phase"] = "message_observe";
            do {
                await Task.Delay(200);
                if (!await PrimaryUnchanged(context) || !ScriptsUnchanged())
                    throw new InvalidOperationException("primary_state_changed");
                result = await RunChat(context.Core, request);
            } while (Convert.ToString(result["state"]) == "waiting" && DateTime.UtcNow < deadline);
        }
        await VerifyCrossChat(context, author);
        }
        } catch (Exception error) { operationError = error; }
        await context.Core.CallDevToolsProtocolMethodAsync("Emulation.setFocusEmulationEnabled",
            "{\"enabled\":false}");
        if (operationError != null) throw operationError;
        await VerifyCrossChat(context, author);
        return result;
    }
    async Task<Dictionary<string, object>> InspectMessage(
        Dictionary<string, object> args, bool mentionDiagnostics = false)
    {
        if (Flag(args, "validate_format"))
        {
            args["phase"] = "validate_payload";
            return await RunChat(args);
        }
        object chatValue, messageValue, openMenuValue;
        if (!args.TryGetValue("chat", out chatValue) ||
            !args.TryGetValue("message_id", out messageValue) || String.IsNullOrEmpty(groupScript))
            throw new InvalidDataException();
        var chat = chatValue as Dictionary<string, object>;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidDataException();
        string chatId = Convert.ToString(chat["chat_id"]), title = Convert.ToString(chat["title"]),
               messageId = Convert.ToString(messageValue);
        bool openMenu = args.TryGetValue("open_menu", out openMenuValue) &&
                        openMenuValue is bool && (bool)openMenuValue;
        if (openMenuValue != null && !(openMenuValue is bool))
            throw new InvalidDataException();
        if (openMenu)
            throw new InvalidOperationException("menu_interaction_unavailable");
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120 ||
            !Regex.IsMatch(messageId, "^message-body-[0-9]{13}$"))
            throw new InvalidDataException();
        if (mentionDiagnostics && !chatId.StartsWith("19:", StringComparison.Ordinal))
            throw new InvalidDataException();
        var context = await BeginCrossChat(mentionDiagnostics);
        string authorName = currentAccountName;
        await Read(new Dictionary<string, object> {
            { "chat", chat }, { "limit", 50 }
        }, context);
        var request = new Dictionary<string, object> {
            { "phase", "inspect_message" }, { "chatId", chatId }, { "title", title },
            { "messageId", messageId }, { "authorName", authorName }, { "menuAction", "" },
            { "mentionDiagnostics", mentionDiagnostics }
        };
        var result = await RunChat(context.Core, request);
        await VerifyCrossChat(context, authorName);
        if (mentionDiagnostics)
        {
            result["chat_id"] = chatId;
            result["title"] = title;
            result["message_id"] = messageId;
        }
        return result;
    }
    async Task<Dictionary<string, object>> Send(Dictionary<string, object> args)
    {
        var allowedArguments = new HashSet<string>(StringComparer.Ordinal) {
            "chat", "body", "body_format", "html", "formatting", "nodes", "request_id"
        };
        if (args.Keys.Any(key => !allowedArguments.Contains(key)))
            throw new InvalidDataException();
        object chatValue, bodyValue, requestValue, rawFormat, rawHtml, rawFormatting, rawNodes;
        if (!args.TryGetValue("chat", out chatValue) || !args.TryGetValue("body", out bodyValue) ||
            !args.TryGetValue("request_id", out requestValue) || String.IsNullOrEmpty(groupScript))
            throw new InvalidDataException();
        string bodyFormat = args.TryGetValue("body_format", out rawFormat)
                                ? rawFormat as string
                                : "plain";
        string html = args.TryGetValue("html", out rawHtml) ? rawHtml as string : null;
        object nodes = args.TryGetValue("nodes", out rawNodes) ? rawNodes : null;
        var formatting = args.TryGetValue("formatting", out rawFormatting)
                             ? rawFormatting as Dictionary<string, object>
                             : null;
        if (formatting == null && rawFormatting == null)
            formatting = new Dictionary<string, object> {
                { "bold", new object[0] }, { "italic", new object[0] },
                { "code", new object[0] }, { "links", new object[0] },
                { "unordered_list", false }, { "ordered_list", false }
            };
        var chat = chatValue as Dictionary<string, object>;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidDataException();
        string chatId = Convert.ToString(chat["chat_id"]), title = Convert.ToString(chat["title"]),
               body = bodyValue as string, requestId = requestValue as string;
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120 ||
            String.IsNullOrEmpty(body) || body.Length > 2000 || String.IsNullOrEmpty(requestId) ||
            !Regex.IsMatch(requestId, "^[A-Za-z0-9._-]{1,128}$") ||
            (bodyFormat != "plain" && bodyFormat != "markdown") || formatting == null ||
            (html != null && html.Length > 12000))
            throw new InvalidDataException();
        var context = await BeginCrossChat();
        string authorName = currentAccountName;
        if (context.ProtectPrimary)
            await OpenExactChatOnAuxiliary(context.Core, chatId, title);
        var validationRequest = new Dictionary<string, object> {
            { "phase", "validate_payload" }, { "body", body }, { "bodyFormat", bodyFormat },
            { "html", html }, { "nodes", nodes }, { "formatting", formatting }
        };
        var payloadCheck = await RunChat(context.Core, validationRequest);
        if (Convert.ToString(payloadCheck["state"]) != "payload_valid")
            throw new InvalidDataException();
        string claims = Path.Combine(Path.GetDirectoryName(profile), "teams-web-sends", "claims");
        Directory.CreateDirectory(claims);
        string digest, bodyDigest, targetDigest;
        using (var sha = SHA256.Create())
        {
            digest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(requestId)))
                         .Replace("-", "")
                         .ToLowerInvariant();
            bodyDigest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(
                             bodyFormat + "\0" + body + "\0" + (html ?? "") + "\0" +
                             Json.Serialize(formatting))))
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
        string phase = context.ProtectPrimary ? "locate" : "navigate";
        string navigationId = Guid.NewGuid().ToString("N");
        var deadline = DateTime.UtcNow.AddSeconds(35);
        while (DateTime.UtcNow < deadline)
        {
            if (!await PrimaryUnchanged(context))
                throw new InvalidOperationException("primary_state_changed");
            var request = new Dictionary<string, object> {
                { "phase", phase }, { "body", body }, { "bodyFormat", bodyFormat },
                { "html", html }, { "nodes", nodes }, { "formatting", formatting }, { "chatId", chatId },
                { "title", title }, { "requestId", requestId }, { "authorName", authorName },
                { "navigationId", navigationId }
            };
            var result = await RunChat(context.Core, request);
            string state = Convert.ToString(result["state"]);
            if (state == "navigated" || state == "ready") phase = "prepare";
            else if (state == "prepared" && bodyFormat == "plain")
            {
                if (!ScriptsUnchanged() || !await PrimaryUnchanged(context))
                    throw new InvalidOperationException("primary_state_changed");
                await context.Core.CallDevToolsProtocolMethodAsync(
                    "Input.insertText", Json.Serialize(new Dictionary<string, object> { { "text", body } }));
                phase = "send";
            }
            else if (state == "prepared" && bodyFormat == "markdown")
                phase = "paste";
            else if (state == "pasted")
                phase = "send";
            else if (state == "clicked") phase = "observe";
            else if (state == "observed")
            {
                if (Convert.ToString(result["chat_id"]) != chatId ||
                    Convert.ToString(result["title"]) != title)
                    throw new InvalidOperationException();
                await VerifyCrossChat(context, authorName);
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
    async Task<Dictionary<string, object>> RunChat(Dictionary<string, object> request)
    {
        return await RunChat(controller.CoreWebView2, request);
    }
    async Task<Dictionary<string, object>> RunChat(
        CoreWebView2 core, Dictionary<string, object> request)
    {
        return await ExecuteChat(core, request);
    }
    async Task<Dictionary<string, object>> EditMessage(Dictionary<string, object> args)
    {
        object chatValue, messageValue, expectedValue, bodyValue, requestValue;
        object rawFormat, rawHtml, rawFormatting, rawNodes;
        if (!args.TryGetValue("chat", out chatValue) ||
            !args.TryGetValue("message_id", out messageValue) ||
            !args.TryGetValue("expected_text", out expectedValue) ||
            !args.TryGetValue("body", out bodyValue) ||
            !args.TryGetValue("request_id", out requestValue) ||
            String.IsNullOrEmpty(groupScript))
            throw new InvalidDataException();
        var chat = chatValue as Dictionary<string, object>;
        if (chat == null || !chat.ContainsKey("chat_id") || !chat.ContainsKey("title"))
            throw new InvalidDataException();
        string chatId = Convert.ToString(chat["chat_id"]), title = Convert.ToString(chat["title"]),
               messageId = messageValue as string, expectedText = expectedValue as string,
               body = bodyValue as string, requestId = requestValue as string;
        string bodyFormat = args.TryGetValue("body_format", out rawFormat)
                                ? rawFormat as string
                                : "plain";
        string html = args.TryGetValue("html", out rawHtml) ? rawHtml as string : null;
        object nodes = args.TryGetValue("nodes", out rawNodes) ? rawNodes : null;
        var formatting = args.TryGetValue("formatting", out rawFormatting)
                             ? rawFormatting as Dictionary<string, object>
                             : null;
        if (formatting == null && rawFormatting == null)
            formatting = new Dictionary<string, object> {
                { "bold", new object[0] }, { "italic", new object[0] },
                { "code", new object[0] }, { "links", new object[0] },
                { "unordered_list", false }, { "ordered_list", false }
            };
        if (!ValidChatId(chatId) || String.IsNullOrWhiteSpace(title) || title.Length > 120 ||
            !Regex.IsMatch(messageId ?? "", "^message-body-[0-9]{13}$") ||
            expectedText == null || expectedText.Length > 2000 || String.IsNullOrEmpty(body) ||
            body.Length > 2000 || String.IsNullOrEmpty(requestId) ||
            !Regex.IsMatch(requestId, "^[A-Za-z0-9._-]{1,128}$") ||
            (bodyFormat != "plain" && bodyFormat != "markdown") || formatting == null ||
            (html != null && html.Length > 12000))
            throw new InvalidDataException();

        var validationRequest = new Dictionary<string, object> {
            { "phase", "validate_payload" }, { "body", body }, { "bodyFormat", bodyFormat },
            { "html", html }, { "nodes", nodes }, { "formatting", formatting }
        };
        var context = await BeginCrossChat();
        string authorName = currentAccountName;
        if (context.ProtectPrimary)
            await OpenExactChatOnAuxiliary(context.Core, chatId, title);
        var payloadCheck = await RunChat(context.Core, validationRequest);
        if (Convert.ToString(payloadCheck["state"]) != "payload_valid")
            throw new InvalidDataException();

        await Read(new Dictionary<string, object> {
            { "chat", chat }, { "limit", 50 }
        }, context);

        string claims = Path.Combine(Path.GetDirectoryName(profile), "teams-web-sends", "claims",
                                     "edits");
        Directory.CreateDirectory(claims);
        string requestDigest, payloadDigest, destinationDigest, messageDigest;
        using (var sha = SHA256.Create())
        {
            requestDigest = BitConverter.ToString(sha.ComputeHash(
                                 Encoding.UTF8.GetBytes(requestId)))
                                .Replace("-", "")
                                .ToLowerInvariant();
            payloadDigest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(
                                Json.Serialize(args))))
                               .Replace("-", "")
                               .ToLowerInvariant();
            destinationDigest = BitConverter.ToString(sha.ComputeHash(
                                    Encoding.UTF8.GetBytes(chatId + "\0" + title)))
                                   .Replace("-", "")
                                   .ToLowerInvariant();
            messageDigest = BitConverter.ToString(sha.ComputeHash(
                                 Encoding.UTF8.GetBytes(messageId)))
                                .Replace("-", "")
                                .ToLowerInvariant();
        }
        string claim = Path.Combine(claims, requestDigest + ".json");
        using (var file = new FileStream(claim, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(
                new Dictionary<string, object> { { "request_sha256", payloadDigest },
                                                 { "destination_sha256", destinationDigest },
                                                 { "message_sha256", messageDigest } }));
            file.Write(bytes, 0, bytes.Length);
            file.Flush(true);
        }

        string phase = "edit_begin", navigationId = Guid.NewGuid().ToString("N");
        var deadline = DateTime.UtcNow.AddSeconds(35);
        bool focusEmulation = false, completed = false;
        Dictionary<string, object> result = null;
        Exception operationError = null;
        try
        {
            await context.Core.CallDevToolsProtocolMethodAsync(
                "Emulation.setFocusEmulationEnabled", Json.Serialize(
                    new Dictionary<string, object> { { "enabled", true } }));
            focusEmulation = true;
            while (DateTime.UtcNow < deadline)
            {
                if (!await PrimaryUnchanged(context) || !ScriptsUnchanged())
                    throw new InvalidOperationException("primary_state_changed");
                var request = new Dictionary<string, object> {
                    { "phase", phase }, { "chatId", chatId }, { "title", title },
                    { "messageId", messageId }, { "expectedText", expectedText },
                    { "body", body }, { "bodyFormat", bodyFormat }, { "html", html },
                    { "nodes", nodes }, { "formatting", formatting }, { "requestId", requestId },
                    { "authorName", authorName }, { "navigationId", navigationId }
                };
                result = await RunChat(context.Core, request);
                string state = Convert.ToString(result["state"]);
                if (state == "waiting")
                {
                    await Task.Delay(150);
                    continue;
                }
                if (phase == "edit_begin" && state == "toolbar_opening")
                    phase = "edit_open";
                else if (phase == "edit_open" && state == "editor_opening")
                    phase = "edit_prepare";
                else if (phase == "edit_prepare" && state == "editor_prepared")
                    phase = "edit_paste";
                else if (phase == "edit_paste" && state == "edit_pasted")
                    phase = "edit_save";
                else if (phase == "edit_save" && state == "edit_clicked")
                {
                    phase = "edit_observe";
                }
                else if (phase == "edit_observe" && state == "edited")
                {
                    if (Convert.ToString(result["chat_id"]) != chatId ||
                        Convert.ToString(result["title"]) != title ||
                        Convert.ToString(result["message_id"]) != messageId ||
                        String.IsNullOrWhiteSpace(Convert.ToString(result["time"])))
                        throw new InvalidOperationException();
                    await VerifyCrossChat(context, authorName);
                    string receipt = claim + ".receipt";
                    using (var file = new FileStream(receipt, FileMode.CreateNew, FileAccess.Write,
                                                     FileShare.None))
                    {
                        byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(result));
                        file.Write(bytes, 0, bytes.Length);
                        file.Flush(true);
                    }
                    completed = true;
                    break;
                }
                else
                    throw new InvalidOperationException(state);
                await Task.Delay(150);
            }
            if (!completed)
                throw new TimeoutException();
        }
        catch (Exception error)
        {
            operationError = error;
        }
        if (focusEmulation)
        {
            try
            {
                await context.Core.CallDevToolsProtocolMethodAsync(
                    "Emulation.setFocusEmulationEnabled", Json.Serialize(
                        new Dictionary<string, object> { { "enabled", false } }));
            }
            catch
            {
            }
        }
        if (operationError != null)
            throw operationError;
        return result;
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
                        string diagnosticReason = Regex.IsMatch(error.Message ?? "",
                            "^[a-z][a-z0-9_]{1,80}$") ? error.Message : "host_operation_failed";
                        string reason = diagnosticReason;
                        var safeReasons = new HashSet<string>(StringComparer.Ordinal) {
                            "draft_exists", "identity_failed", "failed", "foreground_interference",
                            "primary_state_changed", "script_changed_during_operation",
                            "workspace_unavailable", "search_control_unavailable", "lookup_timeout",
                            "lookup_ambiguous_use_email", "lookup_results_unverified",
                            "lookup_selection_unverified", "lookup_resolution_unverified",
                            "copilot_script_timeout", "copilot_not_ready", "copilot_frame_ambiguous",
                            "copilot_guard_failed", "not_ready", "input_failed", "input_unverified",
                            "submit_unavailable", "draft_changed", "query_in_progress",
                            "model_selector_unavailable", "model_menu_ambiguous", "model_menu_unverified",
                            "model_unavailable", "model_selector_timeout", "attachment_input_unavailable",
                            "attachment_limit_exceeded", "attachment_preexisting", "attachment_not_ready",
                            "attachment_file_changed", "upload_outcome_unknown"
                        };
                        if (!safeReasons.Contains(reason))
                            reason = "host_operation_failed";
                        Console.Error.WriteLine("Teams session operation failed: " + diagnosticReason + " (" +
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
        if (auxiliaryController != null)
            auxiliaryController.Close();
        if (copilotController != null)
            copilotController.Close();
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
        if (auxiliaryHost != null)
            auxiliaryHost.Dispose();
        if (copilotHost != null)
            copilotHost.Dispose();
        host.Dispose();
        base.ExitThreadCore();
    }
    public static void Run(string sdk, string profile, string pipe, string generation, int parent)
    {
        Application.Run(new TeamsSession(sdk, profile, pipe, generation, parent));
    }
}
