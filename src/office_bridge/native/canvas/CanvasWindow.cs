using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

public sealed class CanvasWindow : Form
{
    // This address is fulfilled entirely from memory. Deny every other resource.
    const string Page = "https://office-bridge.invalid/messages";
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool SetDllDirectory(string path);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    CoreWebView2Controller controller;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
    Dictionary<string, object> display;
    bool busy, attempted;
    protected override bool ShowWithoutActivation { get { return true; } }

    CanvasWindow()
    {
        Text = "Office Bridge";
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(760, 800);
        MinimumSize = new Size(360, 300);
        Resize += (s, e) => { if (controller != null) controller.Bounds = ClientRectangle; };
    }

    Dictionary<string, object> Dispatch(string python, string source, object click)
    {
        var info = new ProcessStartInfo(python, "-m office_bridge.canvas_dispatch") {
            WorkingDirectory = source, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true
        };
        using (var child = Process.Start(info))
        {
            child.BeginErrorReadLine();
            var payload = Encoding.UTF8.GetBytes(json.Serialize(click));
            using (var stream = child.StandardInput.BaseStream) stream.Write(payload, 0, payload.Length);
            var reply = child.StandardOutput.ReadToEnd();
            child.WaitForExit();
            return json.Deserialize<Dictionary<string, object>>(reply);
        }
    }

    void Connect(CoreWebView2 core, string python, string source, object instance)
    {
        Action<object> post = value => core.PostWebMessageAsJson(json.Serialize(value));
        core.WebMessageReceived += async (s, e) => {
            if (e.Source != Page) return;
            try
            {
                var rpc = json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson);
                if (!Object.Equals(rpc["jsonrpc"], "2.0")) return;
                string method = rpc["method"] as string;
                if (method == "ui/initialize")
                    post(new { jsonrpc = "2.0", id = rpc["id"], result = new {
                        protocolVersion = "2026-01-26", hostCapabilities = new { serverTools = new object() }
                    }});
                else if (method == "ui/notifications/initialized")
                    post(new { jsonrpc = "2.0", method = "ui/notifications/tool-result",
                        @params = new { structuredContent = display }});
                else if (method == "tools/call")
                {
                    var args = (Dictionary<string, object>)rpc["params"];
                    var name = args["name"] as string;
                    if (busy || attempted || (name != "outlook_get_message" && name != "outlook_create_reply_draft")) return;
                    busy = true;
                    attempted = name == "outlook_create_reply_draft";
                    try
                    {
                        var click = new { display, instance, name, arguments = args["arguments"] };
                        var result = await Task.Run(() => Dispatch(python, source, click));
                        if (IsDisposed) return;
                        object content, next;
                        if (result.TryGetValue("structuredContent", out content)
                            && ((Dictionary<string, object>)content).TryGetValue("presentation", out next)
                            && next is Dictionary<string, object>)
                            display = (Dictionary<string, object>)next;
                        post(new { jsonrpc = "2.0", id = rpc["id"], result });
                    }
                    catch
                    {
                        if (!IsDisposed) post(new { jsonrpc = "2.0", id = rpc["id"], error = new {
                            code = -32000, message = "Office action unconfirmed." }});
                    }
                    finally { busy = false; }
                }
            }
            catch { /* Reject malformed messages; never replay a call. */ }
        };
    }

    async void LoadPage(string profile, byte[] payload, int count, bool hidden, string python, string source, Action loaded)
    {
        try
        {
            var envelope = json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(payload));
            var document = Encoding.UTF8.GetBytes((string)envelope["document"]);
            var interactive = envelope.ContainsKey("display") && !String.IsNullOrEmpty(python);
            var csp = "default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'";
            if (interactive) csp += "; script-src 'sha256-" + (string)envelope["script_hash"] + "'";
            var env = await CoreWebView2Environment.CreateAsync(null, profile);
            var options = env.CreateCoreWebView2ControllerOptions();
            options.IsInPrivateModeEnabled = true;
            controller = await env.CreateCoreWebView2ControllerAsync(Handle, options);
            controller.Bounds = ClientRectangle;
            controller.IsVisible = true;
            var core = controller.CoreWebView2;
            core.Settings.IsScriptEnabled = interactive;
            core.Settings.IsWebMessageEnabled = interactive;
            if (interactive) {
                display = (Dictionary<string, object>)envelope["display"];
                Connect(core, python, source, envelope["instance"]);
            }
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.PermissionRequested += (s, e) => { e.State = CoreWebView2PermissionState.Deny; e.Handled = true; };
            core.NewWindowRequested += (s, e) => { e.Handled = true; };
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += (s, e) => {
                e.Response = e.Request.Uri == Page
                    ? env.CreateWebResourceResponse(new MemoryStream(document, false), 200, "OK",
                        "Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nContent-Security-Policy: " + csp)
                    : env.CreateWebResourceResponse(null, 403, "Blocked", "");
            };
            ulong navigation = 0;
            core.NavigationStarting += (s, e) => {
                if (e.Uri == Page && navigation == 0) navigation = e.NavigationId;
                else e.Cancel = true;
            };
            core.NavigationCompleted += async (s, e) => {
                if (navigation == 0 || e.NavigationId != navigation) return;
                try
                {
                    if (!e.IsSuccess) { Close(); return; }
                    string matches = await core.ExecuteScriptAsync(
                        "document.querySelector('main h1') !== null && document.querySelectorAll('article').length === " + count);
                    if (matches != "true") { Close(); return; }
                    if (interactive) {
                        for (int i = 0; i < 100; ++i) {
                            if (await core.ExecuteScriptAsync("document.documentElement.dataset.officeReady === 'true'") == "true") break;
                            if (i == 99) { Close(); return; }
                            await Task.Delay(50);
                        }
                    }
                    if (interactive && hidden && Environment.GetEnvironmentVariable("OFFICE_CANVAS_VERIFY_CLICK") == "1") {
                        // The verifier supplies an unavailable fixture endpoint.
                        await core.ExecuteScriptAsync("document.querySelector('.mail-actions button').click()");
                        for (int i = 0; i < 200; ++i) {
                            if (await core.ExecuteScriptAsync("document.getElementById('action-status').textContent.includes('Office action failed')") == "true") break;
                            if (i == 199) { Close(); return; }
                            await Task.Delay(50);
                        }
                    }
                    if (!hidden) Show();
                    loaded();
                }
                catch { Close(); }
            };
            core.Navigate(Page);
        }
        catch { Close(); }
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        if (controller != null) controller.Close();
        base.OnFormClosed(e);
    }

    static void Reply(string line)
    {
        // The requesting worker may have exited after handing over the document.
        try { Console.Out.WriteLine(line); Console.Out.Flush(); } catch (IOException) { }
    }

    public static void Run(string sdk, string profile, string id, string digest, int count, bool hidden, string python, string source)
    {
        var input = new BinaryReader(Console.OpenStandardInput());
        int size = input.ReadInt32();
        byte[] document = input.ReadBytes(size);
        if (document.Length != size) return;
        string key = "Local\\OfficeBridgeCanvas-" + (hidden ? "verify-" : "") + id;
        using (var gate = new Mutex(false, key))
        using (var ready = new EventWaitHandle(false, EventResetMode.ManualReset, key + "-ready"))
        {
            bool owns;
            try { owns = gate.WaitOne(0); } catch (AbandonedMutexException) { owns = true; }
            if (!owns)
            {
                if (!ready.WaitOne(TimeSpan.FromSeconds(24))) return;
                using (var shared = MemoryMappedFile.OpenExisting(key + "-view", MemoryMappedFileRights.Read))
                using (var reader = new BinaryReader(shared.CreateViewStream(0, 0, MemoryMappedFileAccess.Read)))
                {
                    if (reader.ReadString() != digest) return;
                    int pid = reader.ReadInt32();
                    var window = new IntPtr(reader.ReadInt64());
                    uint owner;
                    if (GetWindowThreadProcessId(window, out owner) == 0 || owner != pid) return;
                    if (!hidden && IsIconic(window)) ShowWindowAsync(window, 4); // SW_SHOWNOACTIVATE
                    Reply("READY " + pid + " " + window.ToInt64() + " " + count);
                }
                return;
            }
            try
            {
                ready.Reset();
                SetDllDirectory(sdk);
                Application.EnableVisualStyles();
                using (var shared = MemoryMappedFile.CreateOrOpen(key + "-view", 256))
                using (var context = new ApplicationContext())
                using (var window = new CanvasWindow())
                using (var timeout = new System.Windows.Forms.Timer { Interval = 24000 })
                {
                    var handle = window.Handle;
                    int pid = Process.GetCurrentProcess().Id;
                    timeout.Tick += (s, e) => window.Close();
                    window.FormClosed += (s, e) => context.ExitThread();
                    timeout.Start();
                    window.BeginInvoke(new Action(() => window.LoadPage(profile, document, count, hidden, python, source, () => {
                        timeout.Stop();
                        using (var writer = new BinaryWriter(shared.CreateViewStream()))
                        {
                            writer.Write(digest); writer.Write(pid); writer.Write(handle.ToInt64());
                        }
                        ready.Set();
                        Reply("READY " + pid + " " + handle.ToInt64() + " " + count);
                    })));
                    Application.Run(context);
                }
            }
            finally { ready.Reset(); gate.ReleaseMutex(); }
        }
    }
}
