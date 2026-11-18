using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
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
    protected override bool ShowWithoutActivation { get { return true; } }

    CanvasWindow()
    {
        Text = "Office Bridge";
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(760, 800);
        MinimumSize = new Size(360, 300);
        Resize += (s, e) => { if (controller != null) controller.Bounds = ClientRectangle; };
    }

    async void LoadPage(string profile, byte[] document, int count, bool hidden, Action loaded)
    {
        try
        {
            var env = await CoreWebView2Environment.CreateAsync(null, profile);
            var options = env.CreateCoreWebView2ControllerOptions();
            options.IsInPrivateModeEnabled = true;
            controller = await env.CreateCoreWebView2ControllerAsync(Handle, options);
            controller.Bounds = ClientRectangle;
            controller.IsVisible = true;
            var core = controller.CoreWebView2;
            core.Settings.IsScriptEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.PermissionRequested += (s, e) => { e.State = CoreWebView2PermissionState.Deny; e.Handled = true; };
            core.NewWindowRequested += (s, e) => { e.Handled = true; };
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += (s, e) => {
                e.Response = e.Request.Uri == Page
                    ? env.CreateWebResourceResponse(new MemoryStream(document, false), 200, "OK",
                        "Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nContent-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'none'")
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

    public static void Run(string sdk, string profile, string id, string digest, int count, bool hidden)
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
                    window.BeginInvoke(new Action(() => window.LoadPage(profile, document, count, hidden, () => {
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
