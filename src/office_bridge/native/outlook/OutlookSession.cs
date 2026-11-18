using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

// This hidden window owns a persistent, isolated Outlook web session.
public sealed class OutlookSession : ApplicationContext
{
    sealed class InboxScanResult
    {
        public List<Dictionary<string, object>> Rows;
        public bool Truncated;
    }

    sealed class VerificationWindow : Form
    {
        protected override bool ShowWithoutActivation { get { return true; } }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern bool SetDllDirectory(string path);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool GetNamedPipeClientComputerName(
        Microsoft.Win32.SafeHandles.SafePipeHandle pipe, StringBuilder name, uint size);

    const int MaxFrame = 1048576;
    static readonly JavaScriptSerializer Json =
        new JavaScriptSerializer { MaxJsonLength = MaxFrame };
    static readonly string[] AllowedHosts = {
        "outlook.office.com", "outlook.office365.com", "outlook.cloud.microsoft", "office.com",
        "office365.com", "microsoftonline.com", "microsoft.com"
    };
    readonly VerificationWindow host = new VerificationWindow();
    readonly string sdk, profile, pipeName, generation, expectedAccount;
    readonly string storeIdentity;
    readonly string sourceHash = Environment.GetEnvironmentVariable("OFFICE_OUTLOOK_SOURCE_HASH") ?? "";
    readonly int parentId;
    readonly bool visible;
    readonly CancellationTokenSource stop = new CancellationTokenSource();
    readonly List<CoreWebView2Frame> outlookFrames = new List<CoreWebView2Frame>();
    CoreWebView2Environment environment;
    CoreWebView2Controller controller;
    FileStream profileLock;
    string error = "loading";
    string navigationHost = "";
    string navigationResult = "not_started";
    string activeOperation = "idle";
    string activeStage = "idle";
    string lastFailureOperation = "";
    string lastFailureStage = "";
    string lastFailureType = "";
    string lastFailureCode = "";
    DateTime responseProbeUntil = DateTime.MinValue;
    string responseProbeConversation = "";
    bool responseProbeBusy;
    int responseProbeMatched;
    int responseProbeGetConversationItems;
    int responseProbeJson;
    int responseProbeOversize;
    int responseProbeReadErrors;
    int responseProbeEmpty;
    int responseProbeItemIds;
    int responseProbeConversationIds;
    int responseProbeConversationMatches;
    int responseProbeChangeKeys;
    int responseProbeBodies;
    readonly HashSet<string> responseItemFields = new HashSet<string>();
    readonly HashSet<string> responseIdentityFields = new HashSet<string>();
    readonly HashSet<string> responseRoutes = new HashSet<string>();
    readonly HashSet<string> responseActions = new HashSet<string>();
    readonly HashSet<string> responseActionRoutes = new HashSet<string>();
    readonly HashSet<string> responseRootFields = new HashSet<string>();
    readonly HashSet<string> responseRestOperations = new HashSet<string>();
    readonly HashSet<string> batchShapes = new HashSet<string>();
    int responseEventCount;
    int responseAcceptedCount;
    int responseParsedCount;
    string responseReadFailure = "";
    string responseReadStage = "";
    bool initializing;
    // Bounded identity metadata from Outlook's own conversation responses. No bodies.
    readonly Dictionary<string, List<Dictionary<string, object>>> observedConversations =
        new Dictionary<string, List<Dictionary<string, object>>>(StringComparer.Ordinal);
    readonly Queue<string> observedOrder = new Queue<string>();
    readonly Dictionary<string, Dictionary<string, object>> observedMessages =
        new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
    readonly HashSet<string> createdDraftIds = new HashSet<string>(StringComparer.Ordinal);
    readonly HashSet<string> movedItemIds = new HashSet<string>(StringComparer.Ordinal);
    CoreWebView2DownloadOperation pendingDownload = null;
    string pendingDownloadPath = "";
    bool allowDownload = false;
    bool mutationStarted = false;

    static readonly HashSet<string> SafeErrors = new HashSet<string>(StringComparer.Ordinal) {
        "invalid_request", "not_ready", "needs_interactive_login", "access_blocked",
        "identity_unverified", "mailbox_not_found", "message_not_found", "stale_message",
        "stale_draft", "unresolved_recipient", "archive_unavailable", "archive_protected",
        "attachment_unavailable", "attachment_limit_exceeded", "draft_exists",
        "mutation_outcome_unknown", "mailbox_dom_unrecognized",
        "message_identity_unavailable", "message_identity_ambiguous", "version_incomplete",
        "compose_unavailable", "compose_control_ambiguous", "composer_unavailable",
        "composer_ambiguous", "recipient_field_unavailable", "subject_field_unavailable",
        "draft_identity_unavailable", "draft_not_saved", "reply_unavailable",
        "reply_control_ambiguous", "send_not_submitted", "outlook_operation_unavailable",
        "not_implemented"
    };

    void SetStage(string value)
    {
        activeStage = value;
    }

    static string SafeErrorCode(string value)
    {
        return value != null && SafeErrors.Contains(value) ? value : "host_operation_failed";
    }

    const string StatusScript = @"(() => {
      const expected=__EXPECTED_ACCOUNT__;
      const openProfile=__OPEN_PROFILE__;
      const visible=e=>!!e && !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
      const host=location.hostname.toLowerCase();
      const appHost=host==='outlook.office.com'||host==='outlook.office365.com'||
        host==='outlook.cloud.microsoft';
      const nodes=[];
      const visit=root=>{for(const e of root.querySelectorAll('*')){
        nodes.push(e);if(e.shadowRoot)visit(e.shadowRoot);
      }};
      visit(document);
      const headers=nodes.filter(e=>e.tagName==='HEADER'||e.getAttribute('role')==='banner');
      const withinHeader=e=>{
        let current=e;
        while(current){
          if(headers.indexOf(current)!==-1)return true;
          const root=current.getRootNode();
          current=current.parentElement||(root&&root.host)||null;
        }
        return false;
      };
      const composedWithin=(e,ancestor)=>{
        let current=e;
        while(current){
          if(current===ancestor)return true;
          const root=current.getRootNode();
          current=current.parentElement||(root&&root.host)||null;
        }
        return false;
      };
      const avatarNodes=nodes.filter(e=>{
        const markers=(e.getAttribute('alt')||'')+' '+(e.getAttribute('aria-label')||'')+
          ' '+(e.getAttribute('data-testid')||'')+' '+
          (e.getAttribute('data-automationid')||'')+' '+String(e.className||'');
        const rect=e.getBoundingClientRect();
        const radius=getComputedStyle(e).borderRadius;
        const round=rect.width>0&&rect.width<=80&&
          Math.abs(rect.width-rect.height)<=4&&/50%|999px/.test(radius);
        return /profile|avatar|account|user photo|user image/i.test(markers)||
          round&&(e.tagName==='IMG'&&!!(e.getAttribute('alt')||'').trim()||
            e.getAttribute('role')==='img'&&!!(e.getAttribute('aria-label')||'').trim());
      });
      const interactive=nodes.filter(e=>(e.tagName==='BUTTON'||e.getAttribute('role')==='button'||
        e.tagName==='A'&&e.tabIndex>=0)&&visible(e));
      const headerControls=interactive.filter(withinHeader);
      const topRightControls=interactive.filter(e=>{
        const rect=e.getBoundingClientRect();
        return rect.left>=innerWidth*.65&&rect.top>=0&&rect.top<=120;
      });
      const profile=interactive.map(e=>{
        const labels=['aria-label','title','data-testid','data-automationid','id']
          .map(k=>e.getAttribute(k)||'').join(' ')+' '+String(e.className||'');
        const rect=e.getBoundingClientRect();
        const rightCorner=rect.left>=innerWidth*.65&&rect.top>=0&&rect.top<=120;
        const avatar=avatarNodes.some(child=>child!==e&&composedWithin(child,e));
        const knownId=/^(O365_MainLink_Me|mectrl_main_trigger|meControl)$/i
          .test(e.id||'');
        const named=/profile|account|avatar|my account|view account|user menu/i.test(labels);
        const relevant=withinHeader(e)||rightCorner;
        const kind=knownId?'microsoft_account_control':
          named&&relevant?'accessible_name':rightCorner&&avatar?'top_right_profile_image':'';
        return kind?{element:e,kind:kind}:null;
      }).filter(Boolean);
      const microsoftAccountTriggers=profile.filter(p=>
        p.kind==='microsoft_account_control');
      const profileTrigger=microsoftAccountTriggers.length===1?
        microsoftAccountTriggers[0]:profile.length===1?profile[0]:null;
      const controlledIds=new Set(profile.flatMap(p=>
        (p.element.getAttribute('aria-controls')||'').split(/\s+/).filter(Boolean)));
      const controlledMenus=nodes.filter(e=>visible(e)&&e.id&&controlledIds.has(e.id));
      const menuNodes=nodes.filter(e=>visible(e)&&(
        e.getAttribute('role')==='menu'||e.getAttribute('role')==='dialog'||
        e.getAttribute('aria-modal')==='true'||
        /account|profile|mectrl/i.test((e.id||'')+' '+
          (e.getAttribute('data-testid')||'')+' '+
          (e.getAttribute('data-automationid')||'')))).concat(controlledMenus);
      const uniqueMenuNodes=Array.from(new Set(menuNodes));
      const menuEvidence=uniqueMenuNodes.map(e=>{
        const emailPattern=/^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i;
        const addresses=new Set();
        const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
        let textNode;
        while((textNode=walker.nextNode())){
          const address=(textNode.nodeValue||'').trim();
          if(emailPattern.test(address)&&visible(textNode.parentElement))
            addresses.add(address.toLowerCase());
        }
        for(const control of e.querySelectorAll('a,[aria-label],[title]')){
          const values=[control.getAttribute('aria-label')||'',control.getAttribute('title')||''];
          const href=control.getAttribute('href')||'';
          if(href.toLowerCase().startsWith('mailto:'))values.push(href.slice(7).split('?')[0]);
          for(const value of values){
            const address=value.trim();
            if(emailPattern.test(address)&&visible(control))addresses.add(address.toLowerCase());
          }
        }
        const emails=Array.from(addresses);
        const signOut=Array.from(e.querySelectorAll(
          'button,[role=button],a,[aria-label],[title]')).some(control=>
            /sign\s?out|log\s?out/i.test((control.getAttribute('aria-label')||'')+' '+
              (control.getAttribute('title')||'')+' '+(control.innerText||'')));
        return {element:e,emailCount:emails.length,expected:emails.length===1&&
          emails[0]===expected,signOut:signOut,hasEmail:emails.length>0};
      }).filter(m=>m.hasEmail&&m.signOut);
      const activeMenus=menuEvidence.filter(m=>
        !menuEvidence.some(child=>child.element!==m.element&&
          composedWithin(child.element,m.element)));
      const accountVerified=appHost&&activeMenus.length===1&&
        activeMenus[0].expected&&activeMenus[0].signOut;
      let profileTriggerClicked=false;
      if(openProfile&&appHost&&profileTrigger&&activeMenus.length===0){
        profileTrigger.element.click();
        profileTriggerClicked=true;
      }
      const login=!!document.querySelector(
        'input[type=email],input[name=loginfmt],input[type=password]');
      const blocked=!!document.querySelector(
        '[data-testid=unauthorized-page],[data-testid=error-page],[data-automationid=errorPage]');
      const options=Array.from(document.querySelectorAll('[role=option]')).filter(visible);
      const expectedOptions=options.filter(e=>
        (e.innerText||'').toLowerCase().includes(expected));
      const visibleFor=selector=>Array.from(document.querySelectorAll(selector)).filter(visible);
      const mailRows=visibleFor('[role=option],[role=row],[data-convid],[data-itemid],[data-item-id]');
      const identityAttributes=['data-convid','data-itemid','data-item-id',
        'data-messageid','data-message-id','data-conversationid','data-mid'];
      const mailRowsWithIdentity=mailRows.filter(e=>
        identityAttributes.some(name=>!!e.getAttribute(name)));
      const identityAttributeCounts={};
      for(const name of identityAttributes)
        identityAttributeCounts[name]=mailRows.filter(e=>!!e.getAttribute(name)).length;
      const rowRoleCounts={};
      for(const role of ['option','row','listitem'])
        rowRoleCounts[role]=mailRows.filter(e=>e.getAttribute('role')===role).length;
      const treeitemsVisible=visibleFor('[role=treeitem]');
      const folderMatches=label=>{
        const pattern=new RegExp('^'+label+'\\b','i');
        return treeitemsVisible.filter(e=>pattern.test(
          (e.getAttribute('aria-label')||e.getAttribute('title')||e.innerText||'')
            .trim())).length;
      };
      const rowSemanticCounts={sender:0,subject:0,date:0,time:0,preview:0};
      for(const row of mailRows){
        const descendants=[row,...row.querySelectorAll('*')];
        for(const e of descendants){
          const markers=['aria-label','title','data-testid','data-automationid','data-tid']
            .map(k=>e.getAttribute(k)||'').join(' ')+' '+String(e.className||'');
          if(/\bsender\b|\bfrom\b/i.test(markers))rowSemanticCounts.sender++;
          if(/\bsubject\b/i.test(markers))rowSemanticCounts.subject++;
          if(/\bdate\b|\breceived\b/i.test(markers))rowSemanticCounts.date++;
          if(/\bpreview\b|\bsnippet\b/i.test(markers))rowSemanticCounts.preview++;
          if(e.tagName==='TIME'||e.hasAttribute('datetime'))rowSemanticCounts.time++;
        }
      }
      const mailControls=interactive.map(e=>({
        element:e,
        labels:['aria-label','title','data-testid','data-automationid','id']
          .map(k=>e.getAttribute(k)||'').join(' '),
      }));
      const mailboxLabelCounts={
        inbox:mailControls.filter(c=>/\binbox\b/i.test(c.labels)).length,
        search:mailControls.filter(c=>/\bsearch\b/i.test(c.labels)).length,
        compose:mailControls.filter(c=>/\bnew mail\b|\bcompose\b/i.test(c.labels)).length,
        sent:mailControls.filter(c=>/\bsent items\b|\bsent\b/i.test(c.labels)).length,
        drafts:mailControls.filter(c=>/\bdrafts\b/i.test(c.labels)).length,
        archive:mailControls.filter(c=>/\barchive\b/i.test(c.labels)).length};
      const searchInputs=visibleFor(
        'input[type=search],input[role=searchbox],[role=searchbox],'+
        'input[placeholder*=search i],input[aria-label*=search i],'+
        '[role=combobox][aria-label*=search i]');
      const mainLandmarks=visibleFor('main,[role=main]');
      const grids=visibleFor('[role=grid]');
      const lists=visibleFor('[role=list]');
      const listboxes=visibleFor('[role=listbox]');
      const treeitems=visibleFor('[role=treeitem]');
      const inboxTreeitems=treeitems.filter(e=>
        /^Inbox\b/i.test((e.getAttribute('aria-label')||e.getAttribute('title')||
          e.innerText||'').trim()));
      const inboxSelectedCount=inboxTreeitems.filter(e=>
        e.getAttribute('aria-selected')==='true'||e.getAttribute('aria-current')==='page'||
        e.getAttribute('aria-checked')==='true'||
        /(^|\s)(selected|is-selected)(\s|$)/i.test(String(e.className||''))||
        Array.from(e.querySelectorAll(
          '[aria-selected=true],[aria-current=page],[aria-checked=true],[data-is-selected=true]'))
          .some(visible)).length;
      const appMarkers=nodes.filter(e=>e.getAttribute('role')==='main'||
        e.tagName==='MAIN'||/app|mail/i.test(
          (e.getAttribute('data-testid')||'')+' '+(e.getAttribute('data-automationid')||''))).length;
      const ready=appHost&&appMarkers>0&&!login;
      return {hostname:host,path:location.pathname,app_host:appHost,ready:ready,login:login,
        blocked:blocked,profile_control_count:profile.length,
        profile_control_trigger_count:profileTrigger?1:0,
        profile_control_trigger_clicked:profileTriggerClicked,
        profile_control_expanded_count:profile.filter(p=>
          p.element.getAttribute('aria-expanded')==='true').length,
        profile_control_has_popup_count:profile.filter(p=>
          /^(menu|listbox|dialog|true)$/i.test(
            p.element.getAttribute('aria-haspopup')||'')).length,
        profile_control_controls_menu_count:controlledMenus.length,
        microsoft_account_control_count:profile.filter(p=>
          p.kind==='microsoft_account_control').length,
        accessible_profile_control_count:profile.filter(p=>
          p.kind==='accessible_name').length,
        profile_candidate_kinds:profile.map(p=>p.kind),
        header_count:headers.length,open_shadow_root_count:nodes.filter(e=>!!e.shadowRoot).length,
        header_interactive_count:headerControls.length,
        top_right_interactive_count:topRightControls.length,
        top_right_profile_control_count:profile.filter(p=>
          p.kind==='top_right_profile_image').length,
        menu_surface_count:activeMenus.length,
        account_menu_present:activeMenus.length>0,account_verified:accountVerified,
        account_menu_matches_expected:activeMenus.filter(m=>m.expected).length,
        sign_out_control_count:activeMenus.filter(m=>m.signOut).length,
        account_menu_email_count:activeMenus.reduce((n,m)=>n+m.emailCount,0),
        option_count:options.length,expected_account_option_count:expectedOptions.length,
        main_landmark_count:mainLandmarks.length,grid_count:grids.length,
        list_count:lists.length,listbox_count:listboxes.length,treeitem_count:treeitems.length,
        inbox_folder_selected_count:inboxSelectedCount,
        mail_row_count:mailRows.length,mail_row_identity_count:mailRowsWithIdentity.length,
        mail_row_identity_attributes:identityAttributeCounts,
        mail_row_roles:rowRoleCounts,search_input_count:searchInputs.length,
        mail_row_semantic_counts:rowSemanticCounts,
        inbox_folder_control_count:folderMatches('Inbox'),
        sent_folder_control_count:folderMatches('Sent Items'),
        drafts_folder_control_count:folderMatches('Drafts'),
        archive_folder_control_count:folderMatches('Archive'),
        mail_control_labels:mailboxLabelCounts,
        app_marker_count:appMarkers};
    })()";

    OutlookSession(string sdkPath, string profilePath, string pipe, string gen, int parent, bool showWindow)
    {
        sdk = sdkPath;
        profile = profilePath;
        pipeName = pipe;
        generation = gen;
        parentId = parent;
        visible = showWindow;
        expectedAccount = (Environment.GetEnvironmentVariable("OFFICE_OUTLOOK_EXPECTED_ACCOUNT") ?? "")
                              .Trim()
                              .ToLowerInvariant();
        using (var sha = System.Security.Cryptography.SHA256.Create())
            storeIdentity = "outlook-web-" + BitConverter.ToString(
                sha.ComputeHash(Encoding.UTF8.GetBytes(expectedAccount)))
                .Replace("-", "").ToLowerInvariant();
        host.ShowInTaskbar = visible;
        host.Text = visible ? "Office Bridge Outlook verification" : "";
        host.StartPosition = FormStartPosition.CenterScreen;
        host.ClientSize = new Size(1200, 900);
        IntPtr unused = host.Handle;
        if (visible) host.Show();
        host.BeginInvoke(new Action(async () => await Initialize()));
        Task.Run(() => Serve());
        Task.Run(() => MonitorParent());
    }

    async Task Initialize()
    {
        if (initializing) return;
        initializing = true;
        try
        {
            if (!Regex.IsMatch(expectedAccount, "^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$"))
                throw new InvalidOperationException("expected_account_unconfigured");
            Directory.CreateDirectory(Path.GetDirectoryName(profile));
            string lockPath = Path.Combine(Path.GetDirectoryName(profile), "outlook-webview2-profile.lock");
            profileLock = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
            profileLock.Lock(0, 1);
            SetDllDirectory(sdk);
            var options = new CoreWebView2EnvironmentOptions { AllowSingleSignOnUsingOSPrimaryAccount = true };
            environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
            controller = await environment.CreateCoreWebView2ControllerAsync(host.Handle);
            var core = controller.CoreWebView2;
            core.IsMuted = false;
            controller.Bounds = new Rectangle(0, 0, 1200, 900);
            controller.IsVisible = true;
            core.Settings.IsWebMessageEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.AreDefaultScriptDialogsEnabled = false;
            core.NewWindowRequested += (s, e) => { e.Handled = true; };
            core.LaunchingExternalUriScheme += (s, e) => { e.Cancel = true; };
            core.FrameCreated += (s, e) => TrackFrame(e.Frame);
            core.PermissionRequested += (s, e) =>
            {
                e.State = CoreWebView2PermissionState.Deny;
                e.Handled = true;
            };
            core.BasicAuthenticationRequested += (s, e) => { e.Cancel = true; };
            core.WebResourceResponseReceived += async (s, e) => await ObserveMailResponse(e);
            core.DownloadStarting += (s, e) =>
            {
                if (!allowDownload || pendingDownload != null ||
                    String.IsNullOrEmpty(pendingDownloadPath))
                {
                    e.Cancel = true;
                    e.Handled = true;
                    return;
                }
                pendingDownload = e.DownloadOperation;
                e.ResultFilePath = pendingDownloadPath;
                e.Handled = true;
            };
            core.NavigationStarting += (s, e) =>
            {
                Uri target;
                if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out target))
                {
                    e.Cancel = true;
                    navigationResult = "invalid_uri";
                    return;
                }
                navigationHost = target.Host;
                if (!AllowedHosts.Any(allowed => target.Host.Equals(allowed, StringComparison.OrdinalIgnoreCase) ||
                    target.Host.EndsWith("." + allowed, StringComparison.OrdinalIgnoreCase)))
                {
                    e.Cancel = true;
                    navigationResult = "blocked_host";
                }
                else navigationResult = "started";
            };
            core.NavigationCompleted += (s, e) =>
            {
                navigationResult = e.IsSuccess ? "success" : "failed_" + e.WebErrorStatus.ToString().ToLowerInvariant();
            };
            core.ProcessFailed += (s, e) =>
            {
                error = "browser_failed";
                host.BeginInvoke(new Action(() => ExitThread()));
            };
            core.Navigate("https://outlook.office.com/mail/");
            error = "loading";
        }
        catch (IOException) { error = "profile_busy"; }
        catch (Exception) { error = "host_unavailable"; }
        finally { initializing = false; }
    }

    static string MailResponseAction(string value)
    {
        Uri uri;
        if (!Uri.TryCreate(value, UriKind.Absolute, out uri) ||
            uri.Scheme != Uri.UriSchemeHttps ||
            !uri.IsDefaultPort || !String.IsNullOrEmpty(uri.UserInfo) ||
            !(uri.Host.Equals("outlook.office.com", StringComparison.OrdinalIgnoreCase) ||
              uri.Host.Equals("outlook.office365.com", StringComparison.OrdinalIgnoreCase) ||
              uri.Host.Equals("outlook.cloud.microsoft", StringComparison.OrdinalIgnoreCase)) ||
            uri.AbsolutePath.Length > 8192 ||
            uri.Query.Length > 4096)
            return "";
        // Outlook's gateway uses opaque RPC paths for its mail data. The
        // reducer below selects message metadata and ignores other payloads.
        if (Regex.IsMatch(uri.AbsolutePath, "^/ows/api/[^/]+(?:/[^/]+)*/?$",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
            return "OutlookApi";
        if (Regex.IsMatch(uri.AbsolutePath, "^/(?:ows/)?(?:api/)?(?:v1\\.0|v2\\.0|beta)/",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant) &&
            Regex.IsMatch(uri.AbsolutePath, "/(?:Microsoft\\.OutlookServices\\.)?(?:messages|items|conversations|conversationitems|GetConversationItems|GetConversationParts|GetConversationMessageParts|GetItem|FindItem)(?:\\(|/|$)",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant) &&
            !Regex.IsMatch(uri.AbsolutePath, "/(?:attachments|\\$value)(?:\\(|/|$)",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
            return "RestItems";
        if (Regex.IsMatch(uri.AbsolutePath, "^/(?:ows/)?api/(?:v1\\.0|v2\\.0|beta)/(?:[^/]+/){0,4}\\$batch/?$",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
            return "MailBatch";
        if (!Regex.IsMatch(uri.AbsolutePath, "^/owa/(?:[a-z0-9_-]{1,128}/)?service\\.svc$",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)) return "";
        string action = "";
        int actionCount = 0;
        foreach (string pair in uri.Query.TrimStart('?').Split('&'))
        {
            int equals = pair.IndexOf('=');
            if (equals <= 0 || !pair.Substring(0, equals).Equals("action",
                StringComparison.OrdinalIgnoreCase)) continue;
            actionCount++;
            action = Uri.UnescapeDataString(pair.Substring(equals + 1));
        }
        // Current Outlook also sends the RPC action outside the URL. Inspect
        // only bounded response bodies; never read request or response headers.
        if (actionCount == 0) return "MailService";
        if (actionCount != 1) return "";
        if (action.Equals("GetConversationItems", StringComparison.OrdinalIgnoreCase))
            return "GetConversationItems";
        foreach (string supported in new[] { "CreateItem", "UpdateItem", "FindItem",
                "GetItem", "MoveItem", "SendItem" })
            if (action.Equals(supported, StringComparison.OrdinalIgnoreCase)) return supported;
        return "";
    }

    void CountResponseShape(object root)
    {
        var pending = new Stack<object>();
        pending.Push(root);
        int visited = 0;
        while (pending.Count > 0 && visited++ < 20000)
        {
            object node = pending.Pop();
            var map = node as Dictionary<string, object>;
            if (map != null)
            {
                if (map.Keys.Any(key => key.Equals("ItemId", StringComparison.OrdinalIgnoreCase) ||
                    key.Equals("ConversationId", StringComparison.OrdinalIgnoreCase) ||
                    key.Equals("ChangeKey", StringComparison.OrdinalIgnoreCase)))
                {
                    foreach (string key in map.Keys.Take(100)) responseItemFields.Add(key);
                    var identity = Field(map, "ItemId") as Dictionary<string, object>;
                    if (identity != null)
                        foreach (string key in identity.Keys) responseIdentityFields.Add(key);
                }
                foreach (var field in map)
                {
                    if (PrivateResponseField(field.Key)) continue;
                    if (field.Key.Equals("ItemId", StringComparison.OrdinalIgnoreCase))
                        responseProbeItemIds++;
                    if (field.Key.Equals("ConversationId", StringComparison.OrdinalIgnoreCase))
                    {
                        responseProbeConversationIds++;
                        string id = field.Value as string;
                        var identity = field.Value as Dictionary<string, object>;
                        if (identity != null)
                            foreach (var part in identity)
                                if (part.Key.Equals("Id", StringComparison.OrdinalIgnoreCase))
                                    id = part.Value as string;
                        if (!String.IsNullOrEmpty(responseProbeConversation) &&
                            String.Equals(id, responseProbeConversation, StringComparison.Ordinal))
                            responseProbeConversationMatches++;
                    }
                    if (field.Key.Equals("ChangeKey", StringComparison.OrdinalIgnoreCase))
                        responseProbeChangeKeys++;
                    if (field.Key.Equals("Body", StringComparison.OrdinalIgnoreCase))
                        responseProbeBodies++;
                    if (field.Value is Dictionary<string, object> ||
                        field.Value is System.Collections.IList) pending.Push(field.Value);
                }
            }
            else
            {
                var list = node as System.Collections.IList;
                if (list != null) foreach (object item in list)
                    if (item is Dictionary<string, object> || item is System.Collections.IList)
                        pending.Push(item);
            }
        }
    }

    static string IdentityText(object raw)
    {
        var map = raw as Dictionary<string, object>;
        string value = map == null ? raw as string : map.ContainsKey("Id") ? map["Id"] as string : null;
        if (String.IsNullOrWhiteSpace(value) || value.Length > 4096 ||
            value.Any(character => Char.IsControl(character))) return "";
        return value;
    }

    static List<Dictionary<string, object>> ObservedRecipients(
        Dictionary<string, object> item, ref bool complete)
    {
        var recipients = new List<Dictionary<string, object>>();
        string[] fields = { "ToRecipients", "CcRecipients" };
        bool present = false;
        for (int index = 0; index < fields.Length; index++)
        {
            if (!item.ContainsKey(fields[index]) || item[fields[index]] == null) continue;
            var values = item[fields[index]] as System.Collections.IList;
            if (values == null || values.Count > 500) { complete = false; continue; }
            present = true;
            foreach (object raw in values)
            {
                var entry = raw as Dictionary<string, object>;
                var mailbox = entry != null && entry.ContainsKey("Mailbox")
                    ? entry["Mailbox"] as Dictionary<string, object> :
                    Field(entry, "EmailAddress") as Dictionary<string, object> ?? entry;
                string address = mailbox == null ? "" :
                    (TextField(mailbox, "EmailAddress") ?? "").Trim().ToLowerInvariant();
                if (Field(mailbox, "EmailAddress") is Dictionary<string, object> || address.Length == 0)
                    address = TextField(mailbox, "Address").Trim().ToLowerInvariant();
                if (!Regex.IsMatch(address, "^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$") ||
                    address.Length > 254)
                {
                    complete = false;
                    continue;
                }
                string name = TextField(mailbox, "Name").Trim();
                if (name.Length == 0 || name.Length > 500) name = address;
                recipients.Add(new Dictionary<string, object> {
                    { "display_name", name }, { "smtp_address", address },
                    { "recipient_type", index + 1 }
                });
            }
        }
        if (!present) complete = false;
        return recipients;
    }

    static Dictionary<string, object> ObservedSender(Dictionary<string, object> item)
    {
        var raw = Field(item, "From") as Dictionary<string, object>;
        if (raw == null) raw = Field(item, "Sender") as Dictionary<string, object>;
        var mailbox = raw == null ? null : Field(raw, "Mailbox") as Dictionary<string, object> ??
            Field(raw, "EmailAddress") as Dictionary<string, object>;
        if (mailbox == null) mailbox = raw;
        string address = TextField(mailbox, "EmailAddress").Trim().ToLowerInvariant();
        if (Field(mailbox, "EmailAddress") is Dictionary<string, object> || address.Length == 0)
            address = TextField(mailbox, "Address").Trim().ToLowerInvariant();
        if (!Regex.IsMatch(address, "^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$") || address.Length > 254)
            return null;
        string name = TextField(mailbox, "Name");
        if (name.Length == 0 || name.Length > 500) name = address;
        return new Dictionary<string, object> {
            { "display_name", name }, { "smtp_address", address },
            { "recipient_type", 1 }
        };
    }

    static List<Dictionary<string, object>> ObservedAttachments(Dictionary<string, object> item)
    {
        var result = new List<Dictionary<string, object>>();
        var list = Field(item, "Attachments") as System.Collections.IList;
        if (list == null || list.Count > 500) return result;
        foreach (object value in list)
        {
            var attachment = value as Dictionary<string, object>;
            string id = IdentityText(Field(attachment, "AttachmentId"));
            if (id.Length == 0) id = IdentityText(Field(attachment, "Id"));
            string name = TextField(attachment, "Name");
            long size;
            if (id.Length == 0 || name.Length == 0 || name.Length > 500 ||
                !Int64.TryParse(TextField(attachment, "Size"), out size) || size < 0)
                continue;
            result.Add(new Dictionary<string, object> {
                { "index", result.Count + 1 }, { "attachment_id", id },
                { "filename", name }, { "size_bytes", size },
                { "inline", BoolField(attachment, "IsInline") }
            });
        }
        return result;
    }

    // Reduce one parsed service response to per-conversation identity records.
    static Dictionary<string, List<Dictionary<string, object>>> ObservedItems(object root)
    {
        var found = new Dictionary<string, List<Dictionary<string, object>>>(StringComparer.Ordinal);
        var pending = new Stack<KeyValuePair<object, string>>();
        pending.Push(new KeyValuePair<object, string>(root, ""));
        int visited = 0, records = 0;
        while (pending.Count > 0 && visited++ < 20000 && records < 200)
        {
            var context = pending.Pop();
            object node = context.Key;
            string inheritedConversation = context.Value;
            var map = node as Dictionary<string, object>;
            if (map == null)
            {
                var list = node as System.Collections.IList;
                if (list != null) foreach (object child in list)
                    if (child is Dictionary<string, object> || child is System.Collections.IList)
                        pending.Push(new KeyValuePair<object, string>(child, inheritedConversation));
                continue;
            }
            string conversation = IdentityText(Field(map, "ConversationId"));
            if (conversation.Length == 0) conversation = inheritedConversation;
            foreach (var field in map)
                if (!PrivateResponseField(field.Key))
                if (field.Value is Dictionary<string, object> ||
                    field.Value is System.Collections.IList)
                    pending.Push(new KeyValuePair<object, string>(field.Value, conversation));
            var identity = Field(map, "ItemId") as Dictionary<string, object>;
            if (identity == null && map.ContainsKey("Id") &&
                (map.ContainsKey("ChangeKey") || Field(map, "IsDraft") is bool))
                identity = new Dictionary<string, object> {
                    { "Id", map["Id"] }, { "ChangeKey", Field(map, "ChangeKey") }
                };
            if (identity == null) continue;
            string entry = IdentityText(identity);
            string changeKey = identity == null || !identity.ContainsKey("ChangeKey")
                ? "" : IdentityText(identity["ChangeKey"]);
            if (entry.Length == 0 || conversation.Length == 0 ||
                changeKey.Length == 0 && !(Field(map, "IsDraft") is bool)) continue;
            bool complete = changeKey.Length > 0 &&
                map.ContainsKey("HasAttachments") && map["HasAttachments"] is bool &&
                map.ContainsKey("IsDraft") && map["IsDraft"] is bool;
            var recipients = ObservedRecipients(map, ref complete);
            var record = new Dictionary<string, object> {
                { "entry_id", entry }, { "change_key", changeKey },
                { "conversation_id", conversation },
                { "is_draft", map.ContainsKey("IsDraft") && map["IsDraft"] is bool &&
                    (bool)map["IsDraft"] },
                { "draft_known", map.ContainsKey("IsDraft") && map["IsDraft"] is bool },
                { "has_attachments", map.ContainsKey("HasAttachments") &&
                    map["HasAttachments"] is bool && (bool)map["HasAttachments"] },
                { "recipients", recipients }, { "complete", complete },
                { "sender", ObservedSender(map) },
                { "subject", TextField(map, "Subject").Length <= 1000 ? TextField(map, "Subject") : "" },
                { "sent_at", TextField(map, "DateTimeSent").Length <= 40 ? TextField(map, "DateTimeSent") : "" },
                { "attachments", ObservedAttachments(map) }
            };
            List<Dictionary<string, object>> items;
            if (!found.TryGetValue(conversation, out items))
                found[conversation] = items = new List<Dictionary<string, object>>();
            if (!items.Any(other => TextField(other, "entry_id") == entry &&
                    TextField(other, "change_key") == changeKey))
            {
                items.Add(record);
                records++;
            }
        }
        return pending.Count == 0 ? found :
            new Dictionary<string, List<Dictionary<string, object>>>(StringComparer.Ordinal);
    }

    void RememberObserved(Dictionary<string, List<Dictionary<string, object>>> found)
    {
        foreach (var pair in found)
        {
            if (!observedConversations.ContainsKey(pair.Key))
            {
                while (observedOrder.Count >= 64)
                    observedConversations.Remove(observedOrder.Dequeue());
                observedOrder.Enqueue(pair.Key);
            }
            observedConversations[pair.Key] = pair.Value;
            foreach (var record in pair.Value)
            {
                if (observedMessages.Count >= 500) observedMessages.Clear();
                observedMessages[TextField(record, "entry_id")] = record;
            }
        }
    }

    void RememberMutationIdentities(object root, string action)
    {
        var pending = new Stack<object>();
        pending.Push(root);
        int visited = 0;
        while (pending.Count > 0 && visited++ < 20000)
        {
            object node = pending.Pop();
            var map = node as Dictionary<string, object>;
            if (map == null)
            {
                var list = node as System.Collections.IList;
                if (list != null) foreach (object child in list) pending.Push(child);
                continue;
            }
            foreach (var field in map)
                if (!PrivateResponseField(field.Key) &&
                    (field.Value is Dictionary<string, object> || field.Value is System.Collections.IList))
                    pending.Push(field.Value);
            string id = IdentityText(Field(map, "ItemId"));
            if (id.Length == 0 && map.ContainsKey("ChangeKey"))
                id = IdentityText(Field(map, "Id"));
            if (id.Length == 0) continue;
            if (action == "CreateItem" &&
                (activeOperation == "outlook_create_reply_draft" ||
                 activeOperation == "outlook_create_mail_draft") && createdDraftIds.Count < 8)
                createdDraftIds.Add(id);
            if (action == "MoveItem" && activeOperation == "outlook_archive" &&
                movedItemIds.Count < 8) movedItemIds.Add(id);
        }
    }

    async Task ObserveMailResponse(CoreWebView2WebResourceResponseReceivedEventArgs e)
    {
        bool probing = DateTime.UtcNow < responseProbeUntil && !responseProbeBusy &&
            responseProbeMatched < 8;
        try
        {
            responseEventCount = Math.Min(responseEventCount + 1, 100000);
            Uri route;
            if (Uri.TryCreate(e.Request.Uri, UriKind.Absolute, out route) &&
                AllowedHosts.Take(3).Contains(route.Host) && responseRoutes.Count < 80)
            {
                string[] safe = { "owa", "ows", "mail", "api", "service.svc", "0", "1", "v1.0", "v2.0", "beta",
                    "users", "me", "messages", "conversations", "conversationitems", "getconversationitems",
                    "getitem", "items", "mailfolders", "conversationparts", "$batch" };
                string path = String.Join("/", route.AbsolutePath.Split('/').Where(value =>
                    value.Length > 0).Select(value => {
                        string lowered = value.ToLowerInvariant();
                        lowered = lowered.Replace("microsoft.outlookservices.", "");
                        string name = lowered.Split('(')[0];
                        return safe.Contains(name) ? name + (lowered.Contains("(") ? "(*)" : "") : "*";
                    }).ToArray());
                responseRoutes.Add(path);
                if (responseRestOperations.Count < 80)
                {
                    string last = route.AbsolutePath.Trim('/').Split('/').LastOrDefault() ?? "";
                    string name = last.Split('(')[0];
                    if (Regex.IsMatch(name, "^[A-Za-z][A-Za-z0-9._-]{0,80}$") &&
                        Regex.IsMatch(name, "get|read|message|mail|item|batch|sync|find|conversation|folder",
                            RegexOptions.IgnoreCase))
                        responseRestOperations.Add(name + ":" + e.Response.StatusCode + ":" + e.Request.Method);
                }
                if (route.AbsolutePath.EndsWith("/service.svc") && responseActions.Count < 80)
                {
                    foreach (string pair in route.Query.TrimStart('?').Split('&'))
                        if (pair.StartsWith("action=", StringComparison.OrdinalIgnoreCase))
                        {
                            string name = Uri.UnescapeDataString(pair.Substring(7));
                            if (Regex.IsMatch(name, "^[A-Za-z]{1,80}$"))
                            {
                                responseActions.Add(name);
                                string[] segments = route.AbsolutePath.Trim('/').Split('/');
                                string kind = segments.Length != 3 ? "root" :
                                    segments[1].Contains("%") ? "escaped_mailbox" :
                                    segments[1].Contains("@") ? "mailbox_address" :
                                    Regex.IsMatch(segments[1], "^[a-z0-9_-]+$", RegexOptions.IgnoreCase)
                                        ? "slug" : "other";
                                if (responseActionRoutes.Count < 100)
                                    responseActionRoutes.Add(name + ":" + e.Response.StatusCode +
                                        ":" + kind + ":" + route.Query.Length);
                            }
                        }
                }
            }
            string action = MailResponseAction(e.Request.Uri);
            if (String.IsNullOrEmpty(action) || e.Response.StatusCode != 200) return;
            responseAcceptedCount++;
            if (probing)
            {
                responseProbeBusy = true;
                responseProbeMatched++;
                responseProbeGetConversationItems++;
            }
            responseReadStage = "response_content";
            using (Stream stream = await e.Response.GetContentAsync())
            {
                if (stream == null) { if (probing) responseProbeEmpty++; return; }
                using (var memory = new MemoryStream())
                {
                    byte[] buffer = new byte[16384];
                    int read;
                    while ((read = await stream.ReadAsync(buffer, 0, buffer.Length)) > 0)
                    {
                        if (memory.Length + read > MaxFrame)
                        {
                            if (probing) responseProbeOversize++;
                            return;
                        }
                        memory.Write(buffer, 0, read);
                    }
                    if (memory.Length == 0) { if (probing) responseProbeEmpty++; return; }
                    responseReadStage = "json_parse";
                    object parsed = DeserializeMailResponse(Encoding.UTF8.GetString(memory.ToArray()));
                    if (parsed == null) return;
                    responseParsedCount++;
                    var rootMap = parsed as Dictionary<string, object>;
                    if (rootMap != null)
                        foreach (string key in rootMap.Keys.Take(40))
                            if (responseRootFields.Count < 100) responseRootFields.Add(key);
                    if (action == "MailService")
                    {
                        string kind = TextField(rootMap, "_outlook_type");
                        foreach (string candidate in new[] { "CreateItem", "UpdateItem", "MoveItem",
                            "SendItem", "GetConversationItems", "GetItem", "FindItem" })
                            if (Regex.IsMatch(kind, "^" + candidate + "(?:Json)?Response(?:[:#]|$)"))
                                action = candidate;
                    }
                    if (action == "MailBatch")
                    {
                        DescribeBatch(parsed);
                        parsed = MailBatchBodies(parsed);
                    }
                    responseReadStage = "identity_reduce";
                    var found = ObservedItems(parsed);
                    if (responseParsedCount <= 20) CountResponseShape(parsed);
                    string mutationAction = action;
                    if (action == "RestItems" && e.Request.Method == "POST")
                        mutationAction = activeOperation == "outlook_archive" ? "MoveItem" : "CreateItem";
                    RememberMutationIdentities(parsed, mutationAction);
                    if (host.InvokeRequired)
                        host.BeginInvoke(new Action(() => RememberObserved(found)));
                    else RememberObserved(found);
                    if (probing)
                    {
                        responseProbeJson++;
                        CountResponseShape(parsed);
                    }
                }
            }
        }
        catch (Exception exception)
        {
            responseReadFailure = exception.GetType().Name;
            if (probing) responseProbeReadErrors++;
        }
        finally { if (probing) responseProbeBusy = false; }
    }

    Dictionary<string, object> ResponseProbeStatus()
    {
        return new Dictionary<string, object> {
            { "armed", DateTime.UtcNow < responseProbeUntil },
            { "matched_response_count", responseProbeMatched },
            { "get_conversation_items_count", responseProbeGetConversationItems },
            { "json_response_count", responseProbeJson },
            { "oversize_response_count", responseProbeOversize },
            { "read_error_count", responseProbeReadErrors },
            { "empty_response_count", responseProbeEmpty },
            { "item_id_field_count", responseProbeItemIds },
            { "conversation_id_field_count", responseProbeConversationIds },
            { "selected_conversation_match_count", responseProbeConversationMatches },
            { "change_key_field_count", responseProbeChangeKeys },
            { "body_field_count", responseProbeBodies }
            ,{ "item_fields", responseItemFields.OrderBy(value => value).Take(100).ToArray() }
            ,{ "identity_fields", responseIdentityFields.OrderBy(value => value).Take(20).ToArray() }
            ,{ "response_event_count", responseEventCount }
            ,{ "accepted_response_count", responseAcceptedCount }
            ,{ "parsed_response_count", responseParsedCount }
            ,{ "read_failure_type", responseReadFailure }
            ,{ "read_stage", responseReadStage }
            ,{ "routes", responseRoutes.ToArray() }
            ,{ "actions", responseActions.ToArray() }
            ,{ "action_routes", responseActionRoutes.ToArray() }
            ,{ "source_sha256", sourceHash }
            ,{ "root_fields", responseRootFields.ToArray() }
            ,{ "rest_operations", responseRestOperations.ToArray() }
            ,{ "batch_shapes", batchShapes.ToArray() }
        };
    }

    static object DeserializeMailResponse(string text)
    {
        // Outlook includes WCF __type discriminators. JavaScriptSerializer tries
        // to instantiate those types even for DeserializeObject. Keep them inert.
        // Escaped quotes inside JSON string values cannot match a property key.
        string inert = Regex.Replace(text, "([\\{,])\\s*\"__type\"\\s*:",
            "$1\"_outlook_type\":", RegexOptions.CultureInvariant);
        var parser = new JavaScriptSerializer { MaxJsonLength = MaxFrame, RecursionLimit = 64 };
        object parsed = parser.DeserializeObject(inert);
        NormalizeMailFields(parsed);
        return parsed;
    }

    static void NormalizeMailFields(object root)
    {
        string[] names = { "ItemId", "ConversationId", "ChangeKey", "Id", "IsDraft",
            "HasAttachments", "ToRecipients", "CcRecipients", "BccRecipients", "From", "Sender",
            "Mailbox", "EmailAddress", "Address", "Name", "Subject", "DateTimeSent",
            "Attachments", "AttachmentId", "Size", "IsInline" };
        var pending = new Stack<object>();
        pending.Push(root);
        int visited = 0;
        while (pending.Count > 0 && visited++ < 20000)
        {
            object node = pending.Pop();
            var map = node as Dictionary<string, object>;
            if (map == null)
            {
                var list = node as System.Collections.IList;
                if (list != null) foreach (object child in list) pending.Push(child);
                continue;
            }
            foreach (string key in map.Keys.ToArray())
            {
                string canonical = names.FirstOrDefault(name =>
                    name.Equals(key, StringComparison.OrdinalIgnoreCase));
                if (key == "sentDateTime" || key == "SentDateTime") canonical = "DateTimeSent";
                if (canonical != null && canonical != key && !map.ContainsKey(canonical))
                {
                    map[canonical] = map[key];
                    map.Remove(key);
                }
            }
            foreach (var field in map)
                if (!PrivateResponseField(field.Key) &&
                    (field.Value is Dictionary<string, object> || field.Value is System.Collections.IList))
                    pending.Push(field.Value);
        }
    }

    static bool PrivateResponseField(string key)
    {
        return key.Equals("Headers", StringComparison.OrdinalIgnoreCase) ||
            key.Equals("Cookies", StringComparison.OrdinalIgnoreCase) ||
            key.IndexOf("token", StringComparison.OrdinalIgnoreCase) >= 0 ||
            key.Equals("Authorization", StringComparison.OrdinalIgnoreCase);
    }

    static object MailBatchBodies(object root)
    {
        var map = root as Dictionary<string, object>;
        var responses = (Field(map, "responses") ?? Field(map, "Responses")) as System.Collections.IList;
        var bodies = new List<object>();
        if (responses == null || responses.Count > 100) return bodies;
        foreach (object raw in responses)
        {
            var response = raw as Dictionary<string, object>;
            int status;
            if (!Int32.TryParse(Convert.ToString(Field(response, "status") ??
                Field(response, "Status")), out status) || status != 200) continue;
            object body = Field(response, "body") ?? Field(response, "Body");
            if (body is Dictionary<string, object> || body is System.Collections.IList)
                bodies.Add(body);
        }
        return bodies;
    }

    void DescribeBatch(object root)
    {
        var map = root as Dictionary<string, object>;
        object value = Field(map, "responses") ?? Field(map, "Responses");
        if (batchShapes.Count < 30)
            batchShapes.Add("responses:" + (value == null ? "null" : value.GetType().Name));
        var responses = value as System.Collections.IList;
        if (responses == null) return;
        foreach (object raw in responses.Cast<object>().Take(5))
        {
            var response = raw as Dictionary<string, object>;
            if (response == null || batchShapes.Count >= 30) continue;
            batchShapes.Add("response_fields:" + String.Join(",", response.Keys.Where(key =>
                !PrivateResponseField(key) && Regex.IsMatch(key, "^[A-Za-z_][A-Za-z0-9_]{0,40}$"))
                .Take(30).ToArray()));
            foreach (var field in response)
            {
                if (PrivateResponseField(field.Key) || !Regex.IsMatch(field.Key,
                    "^(?:body|responseBody|status|statusCode)$", RegexOptions.IgnoreCase)) continue;
                string kind = field.Value == null ? "null" : field.Value.GetType().Name;
                var body = field.Value as Dictionary<string, object>;
                if (body != null) kind += ":" + String.Join(",", body.Keys.Where(key =>
                    !PrivateResponseField(key) && Regex.IsMatch(key, "^[A-Za-z_][A-Za-z0-9_]{0,40}$"))
                    .Take(30).ToArray());
                string text = field.Value as string;
                if (text != null) kind += ":json=" + (text.TrimStart().StartsWith("{") ||
                    text.TrimStart().StartsWith("[")).ToString();
                if (batchShapes.Count < 30) batchShapes.Add(field.Key + ":" + kind);
            }
        }
    }

    void TrackFrame(CoreWebView2Frame frame)
    {
        if (outlookFrames.Count >= 100) return;
        outlookFrames.Add(frame);
        frame.FrameCreated += (s, e) => TrackFrame(e.Frame);
        frame.Destroyed += (s, e) => outlookFrames.Remove(frame);
    }

    static bool IsOutlookAppHost(string value)
    {
        return value.Equals("outlook.office.com", StringComparison.OrdinalIgnoreCase) ||
            value.Equals("outlook.office365.com", StringComparison.OrdinalIgnoreCase) ||
            value.Equals("outlook.cloud.microsoft", StringComparison.OrdinalIgnoreCase);
    }

    async Task<Dictionary<string, object>> InspectSurface(Func<string, Task<string>> execute)
    {
        string expected = Json.Serialize(expectedAccount);
        string script = StatusScript.Replace("__EXPECTED_ACCOUNT__", expected)
            .Replace("__OPEN_PROFILE__", "true");
        string raw = await execute(script).ConfigureAwait(true);
        var data = Json.Deserialize<Dictionary<string, object>>(raw);
        bool appHost = data.ContainsKey("app_host") && Convert.ToBoolean(data["app_host"]);
        int triggerCount = data.ContainsKey("profile_control_trigger_count")
            ? Convert.ToInt32(data["profile_control_trigger_count"]) : 0;
        int menuCount = data.ContainsKey("menu_surface_count")
            ? Convert.ToInt32(data["menu_surface_count"]) : 0;
        bool clicked = data.ContainsKey("profile_control_trigger_clicked") &&
            Convert.ToBoolean(data["profile_control_trigger_clicked"]);
        if (appHost && triggerCount == 1 && menuCount == 0 && clicked)
        {
            string passiveScript = script.Replace("__OPEN_PROFILE__", "false");
            var deadline = DateTime.UtcNow.AddSeconds(2);
            do
            {
                await Task.Delay(200);
                raw = await execute(passiveScript).ConfigureAwait(true);
                data = Json.Deserialize<Dictionary<string, object>>(raw);
                menuCount = data.ContainsKey("menu_surface_count")
                    ? Convert.ToInt32(data["menu_surface_count"]) : 0;
            }
            while (menuCount == 0 && DateTime.UtcNow < deadline);
        }
        return data;
    }

    async Task<Dictionary<string, object>> Inspect()
    {
        SetStage("identity_status");
        if (controller == null || controller.CoreWebView2 == null)
            return new Dictionary<string, object> { { "state", error } };
        var main = await InspectSurface(script => controller.CoreWebView2.ExecuteScriptAsync(script));
        var surfaces = new List<Dictionary<string, object>> { main };
        foreach (var frame in outlookFrames.ToArray())
        {
            if (frame.IsDestroyed() != 0) continue;
            try
            {
                string frameHost = Json.Deserialize<string>(
                    await frame.ExecuteScriptAsync("location.hostname.toLowerCase()"));
                if (!IsOutlookAppHost(frameHost)) continue;
                var surface = await InspectSurface(script => frame.ExecuteScriptAsync(script));
                if (surface.ContainsKey("app_host") && Convert.ToBoolean(surface["app_host"]))
                    surfaces.Add(surface);
            }
            catch { }
        }
        var accountMenus = surfaces.Where(s =>
            s.ContainsKey("account_menu_present") && Convert.ToBoolean(s["account_menu_present"]))
            .ToList();
        bool verified = accountMenus.Count == 1 &&
            Convert.ToBoolean(accountMenus[0]["account_verified"]);
        string state = verified ? "authenticated" :
            Convert.ToBoolean(main["login"]) ? "needs_interactive_login" :
            Convert.ToBoolean(main["blocked"]) ? "access_blocked" :
            Convert.ToBoolean(main["ready"]) ? "identity_unverified" : error;
        int profileCount = surfaces.Where(s => s.ContainsKey("profile_control_count"))
            .Sum(s => Convert.ToInt32(s["profile_control_count"]));
        int headerControlCount = surfaces.Sum(s =>
            Convert.ToInt32(s["header_interactive_count"]));
        int headerCount = surfaces.Sum(s =>
            Convert.ToInt32(s["header_count"]));
        int openShadowRootCount = surfaces.Sum(s =>
            Convert.ToInt32(s["open_shadow_root_count"]));
        int topRightControlCount = surfaces.Sum(s =>
            Convert.ToInt32(s["top_right_interactive_count"]));
        int topRightProfileControlCount = surfaces.Sum(s =>
            Convert.ToInt32(s["top_right_profile_control_count"]));
        int expandedProfileControlCount = surfaces.Sum(s =>
            Convert.ToInt32(s["profile_control_expanded_count"]));
        int popupProfileControlCount = surfaces.Sum(s =>
            Convert.ToInt32(s["profile_control_has_popup_count"]));
        int controlledProfileMenuCount = surfaces.Sum(s =>
            Convert.ToInt32(s["profile_control_controls_menu_count"]));
        int profileTriggerCount = surfaces.Sum(s =>
            Convert.ToInt32(s["profile_control_trigger_count"]));
        int microsoftAccountControlCount = surfaces.Sum(s =>
            Convert.ToInt32(s["microsoft_account_control_count"]));
        int accessibleProfileControlCount = surfaces.Sum(s =>
            Convert.ToInt32(s["accessible_profile_control_count"]));
        int mainLandmarkCount = Convert.ToInt32(main["main_landmark_count"]);
        int gridCount = Convert.ToInt32(main["grid_count"]);
        int listCount = Convert.ToInt32(main["list_count"]);
        int listboxCount = Convert.ToInt32(main["listbox_count"]);
        int treeitemCount = Convert.ToInt32(main["treeitem_count"]);
        int inboxSelectedCount = Convert.ToInt32(main["inbox_folder_selected_count"]);
        int mailRowCount = Convert.ToInt32(main["mail_row_count"]);
        int mailRowIdentityCount = Convert.ToInt32(main["mail_row_identity_count"]);
        int searchInputCount = Convert.ToInt32(main["search_input_count"]);
        int inboxFolderControlCount = Convert.ToInt32(main["inbox_folder_control_count"]);
        int sentFolderControlCount = Convert.ToInt32(main["sent_folder_control_count"]);
        int draftsFolderControlCount = Convert.ToInt32(main["drafts_folder_control_count"]);
        int archiveFolderControlCount = Convert.ToInt32(main["archive_folder_control_count"]);
        var mailRowIdentityAttributes = main["mail_row_identity_attributes"];
        var mailRowRoles = main["mail_row_roles"];
        var mailRowSemanticCounts = main["mail_row_semantic_counts"];
        var mailControlLabels = main["mail_control_labels"];
        int signOutCount = accountMenus.Where(s => s.ContainsKey("sign_out_control_count"))
            .Sum(s => Convert.ToInt32(s["sign_out_control_count"]));
        int emailEvidenceCount = accountMenus.Sum(s =>
            Convert.ToInt32(s["account_menu_email_count"]));
        int expectedMatches = accountMenus.Sum(s =>
            Convert.ToInt32(s["account_menu_matches_expected"]));
        return new Dictionary<string, object> {
            { "state", state }, { "account_verified", verified }, { "backend", "webview2" },
            { "last_failure_operation", lastFailureOperation },
            { "last_failure_stage", lastFailureStage },
            { "last_failure_type", lastFailureType },
            { "last_failure_code", lastFailureCode },
            { "host_visible", visible }, { "control_visible", controller.IsVisible },
            { "host_focused", host.ContainsFocus },
            { "foreground_interference", false }, { "restart_required", false },
            { "audio_muted", controller.CoreWebView2.IsMuted },
            { "generation", generation }, { "hostname", main["hostname"] },
            { "source_sha256", sourceHash },
            { "navigation_host", navigationHost }, { "navigation_result", navigationResult },
            { "profile_control_count", profileCount },
            { "profile_control_trigger_count", profileTriggerCount },
            { "header_count", headerCount },
            { "header_interactive_count", headerControlCount },
            { "open_shadow_root_count", openShadowRootCount },
            { "top_right_interactive_count", topRightControlCount },
            { "top_right_profile_control_count", topRightProfileControlCount },
            { "profile_control_expanded_count", expandedProfileControlCount },
            { "profile_control_has_popup_count", popupProfileControlCount },
            { "profile_control_controls_menu_count", controlledProfileMenuCount },
            { "microsoft_account_control_count", microsoftAccountControlCount },
            { "accessible_profile_control_count", accessibleProfileControlCount },
            { "profile_surface_count", surfaces.Count },
            { "account_menu_surface_count", accountMenus.Count },
            { "account_menu_email_count", emailEvidenceCount },
            { "account_menu_matches_expected", expectedMatches },
            { "frame_count", outlookFrames.Count },
            { "app_marker_count", main["app_marker_count"] },
            { "ready", main["ready"] }, { "login", main["login"] },
            { "option_count", main["option_count"] },
            { "expected_account_option_count", main["expected_account_option_count"] },
            { "main_landmark_count", mainLandmarkCount },
            { "grid_count", gridCount }, { "list_count", listCount },
            { "listbox_count", listboxCount }, { "treeitem_count", treeitemCount },
            { "inbox_folder_selected_count", inboxSelectedCount },
            { "mail_row_count", mailRowCount },
            { "mail_row_identity_count", mailRowIdentityCount },
            { "mail_row_identity_attributes", mailRowIdentityAttributes },
            { "mail_row_roles", mailRowRoles },
            { "mail_row_semantic_counts", mailRowSemanticCounts },
            { "search_input_count", searchInputCount },
            { "inbox_folder_control_count", inboxFolderControlCount },
            { "sent_folder_control_count", sentFolderControlCount },
            { "drafts_folder_control_count", draftsFolderControlCount },
            { "archive_folder_control_count", archiveFolderControlCount },
            { "mail_control_labels", mailControlLabels },
            { "sign_out_control_count", signOutCount }
        };
    }

    async Task<Dictionary<string, object>> RequireAuthenticated()
    {
        Dictionary<string, object> status = null;
        DateTime deadline = DateTime.UtcNow.AddSeconds(30);
        while (DateTime.UtcNow < deadline)
        {
            status = await Inspect();
            if (status.ContainsKey("account_verified") &&
                Convert.ToBoolean(status["account_verified"]))
                return status;
            string state = status.ContainsKey("state")
                ? Convert.ToString(status["state"]) : "identity_unverified";
            if (state != "identity_unverified" && state != "loading") break;
            await Task.Delay(400);
        }
        string failed = status != null && status.ContainsKey("state")
            ? Convert.ToString(status["state"]) : "identity_unverified";
        throw new InvalidOperationException(failed == "loading" ? "not_ready" : failed);
    }

    async Task<Dictionary<string, object>> ExecuteMailScript(Dictionary<string, object> request)
    {
        SetStage("mail_script_" + TextField(request, "phase"));
        if (controller == null || controller.CoreWebView2 == null)
            throw new InvalidOperationException("not_ready");
        string scriptPath = Environment.GetEnvironmentVariable("OFFICE_OUTLOOK_SCRIPT_FILE");
        if (String.IsNullOrWhiteSpace(scriptPath) || !File.Exists(scriptPath))
            throw new InvalidOperationException("not_ready");
        string source = File.ReadAllText(scriptPath, Encoding.UTF8);
        string script = source.Replace("__REQUEST__", Json.Serialize(request));
        string raw = await controller.CoreWebView2.ExecuteScriptAsync(script).ConfigureAwait(true);
        var data = Json.Deserialize<Dictionary<string, object>>(raw);
        if (data == null || !data.ContainsKey("state"))
            throw new InvalidOperationException("mailbox_dom_unrecognized");
        return data;
    }

    string RequireStore(object raw)
    {
        string value = Convert.ToString(raw);
        if (!String.Equals(value, storeIdentity, StringComparison.Ordinal))
            throw new InvalidOperationException("mailbox_not_found");
        return value;
    }

    Dictionary<string, object> RequireReference(object raw)
    {
        var reference = raw as Dictionary<string, object>;
        if (reference == null || !reference.ContainsKey("store_id"))
            throw new InvalidOperationException("invalid_request");
        string entryId = TextField(reference, "entry_id");
        string conversationId = TextField(reference, "conversation_id");
        if (String.IsNullOrWhiteSpace(entryId) && String.IsNullOrWhiteSpace(conversationId))
            throw new InvalidOperationException("invalid_request");
        if ((!String.IsNullOrWhiteSpace(entryId) &&
             (entryId.Length > 4096 || entryId.Any(character => Char.IsControl(character)))) ||
            (!String.IsNullOrWhiteSpace(conversationId) &&
             (conversationId.Length > 4096 ||
              conversationId.Any(character => Char.IsControl(character)))))
            throw new InvalidOperationException("invalid_request");
        RequireStore(reference["store_id"]);
        return new Dictionary<string, object> {
            { "entry_id", String.IsNullOrWhiteSpace(entryId) ? null : entryId },
            { "conversation_id", String.IsNullOrWhiteSpace(conversationId) ? null : conversationId },
            { "store_id", storeIdentity }
        };
    }

    static object Field(Dictionary<string, object> value, string key)
    {
        object result;
        return value != null && value.TryGetValue(key, out result) ? result : null;
    }

    static string TextField(Dictionary<string, object> value, string key)
    {
        return Convert.ToString(Field(value, key)) ?? "";
    }

    static bool BoolField(Dictionary<string, object> value, string key)
    {
        object valueObject = Field(value, key);
        return valueObject != null && Convert.ToBoolean(valueObject);
    }

    string ConversationId(Dictionary<string, object> row)
    {
        string entry = TextField(row, "entry_id");
        if (String.IsNullOrWhiteSpace(entry) || entry.Length > 4096 ||
            entry.Any(character => Char.IsControl(character)))
            throw new InvalidOperationException("mailbox_dom_unrecognized");
        return entry;
    }

    static string DigestValues(params string[] values)
    {
        var canonical = new StringBuilder();
        foreach (string value in values)
        {
            string item = value ?? "";
            canonical.Append(item.Length.ToString(CultureInfo.InvariantCulture));
            canonical.Append(':');
            canonical.Append(item);
            canonical.Append('\n');
        }
        using (var sha = System.Security.Cryptography.SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(
                Encoding.UTF8.GetBytes(canonical.ToString()))).Replace("-", "").ToLowerInvariant();
    }

    static List<Dictionary<string, object>> RecipientRows(object raw)
    {
        var values = raw as System.Collections.IList;
        var recipients = new List<Dictionary<string, object>>();
        if (values == null) return recipients;
        foreach (object item in values)
        {
            var recipient = item as Dictionary<string, object>;
            if (recipient == null) continue;
            string address = TextField(recipient, "smtp_address").Trim().ToLowerInvariant();
            string name = TextField(recipient, "display_name").Trim();
            int type;
            if (!Regex.IsMatch(address, "^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$") ||
                !Int32.TryParse(Convert.ToString(Field(recipient, "recipient_type")), out type) ||
                type < 1 || type > 3)
                throw new InvalidOperationException("unresolved_recipient");
            if (String.IsNullOrWhiteSpace(name)) name = address;
            recipients.Add(new Dictionary<string, object> {
                { "display_name", name }, { "smtp_address", address },
                { "recipient_type", type }
            });
        }
        return recipients;
    }

    string DraftDigest(
        string subject, string body, List<Dictionary<string, object>> recipients)
    {
        string addresses = String.Join("\n", recipients.Select(recipient =>
            Convert.ToString(recipient["recipient_type"]) + ":" +
            Convert.ToString(recipient["smtp_address"])).ToArray());
        return DigestValues(subject, body, addresses, expectedAccount, "");
    }

    static string RecipientKey(List<Dictionary<string, object>> recipients)
    {
        return String.Join("\n", recipients.Select(recipient =>
            TextField(recipient, "recipient_type") + ":" +
            TextField(recipient, "smtp_address").ToLowerInvariant()).OrderBy(value => value).ToArray());
    }

    async Task RefreshMailbox()
    {
        var composer = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "composer_state" }
        });
        if (TextField(composer, "state") != "waiting")
            throw new InvalidOperationException("draft_exists");
        observedConversations.Clear();
        observedMessages.Clear();
        observedOrder.Clear();
        controller.CoreWebView2.Reload();
        await Task.Delay(1000);
        await RequireAuthenticated();
    }

    async Task<Dictionary<string, object>> WaitForComposer(int timeoutSeconds)
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
        Dictionary<string, object> state = null;
        while (DateTime.UtcNow < deadline)
        {
            state = await ExecuteMailScript(new Dictionary<string, object> {
                { "phase", "composer_state" }
            });
            string result = TextField(state, "state");
            if (result == "composer_ready") return state;
            if (result != "waiting")
                throw new InvalidOperationException(result);
            await Task.Delay(200);
        }
        throw new InvalidOperationException("not_ready");
    }

    async Task<List<Dictionary<string, object>>> SelectRecipients(string expectedAddress)
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(8);
        while (DateTime.UtcNow < deadline)
        {
            var result = await ExecuteMailScript(new Dictionary<string, object> {
                { "phase", "select_recipient" }, { "recipient_email", expectedAddress }
            });
            string state = TextField(result, "state");
            if (state == "recipient_selected")
            {
                var stateResult = await WaitForComposer(3);
                return RecipientRows(Field(stateResult, "recipients"));
            }
            if (state != "unresolved_recipient")
                throw new InvalidOperationException(state);
            await Task.Delay(200);
        }
        throw new InvalidOperationException("unresolved_recipient");
    }

    Dictionary<string, object> DraftReference(Dictionary<string, object> snapshot)
    {
        string entry = TextField(snapshot, "entry_id");
        string conversation = TextField(snapshot, "conversation_id");
        if (String.IsNullOrWhiteSpace(entry))
            throw new InvalidOperationException("mutation_outcome_unknown");
        return new Dictionary<string, object> {
            { "entry_id", entry },
            { "conversation_id", String.IsNullOrWhiteSpace(conversation) ? null : conversation },
            { "store_id", storeIdentity }
        };
    }

    async Task<Dictionary<string, object>> FinishDraft(
        string subject, string body, List<Dictionary<string, object>> expectedRecipients,
        bool reply, Dictionary<string, object> source)
    {
        var actual = await WaitForComposer(5);
        var recipients = RecipientRows(Field(actual, "recipients"));
        if (String.IsNullOrWhiteSpace(TextField(actual, "subject")) ||
            TextField(actual, "body_text") != body ||
            Convert.ToInt32(Field(actual, "attachment_count")) != 0 ||
            recipients.Count == 0)
            throw new InvalidOperationException("mutation_outcome_unknown");
        if (!String.IsNullOrWhiteSpace(subject) &&
            TextField(actual, "subject") != subject)
            throw new InvalidOperationException("mutation_outcome_unknown");
        if (RecipientKey(recipients) != RecipientKey(expectedRecipients))
            throw new InvalidOperationException("mutation_outcome_unknown");
        string digest = DraftDigest(TextField(actual, "subject"), body, recipients);
        SetStage("draft_save_wait");
        DateTime deadline = DateTime.UtcNow.AddSeconds(20);
        Dictionary<string, object> closed = null;
        while (DateTime.UtcNow < deadline)
        {
            closed = await ExecuteMailScript(new Dictionary<string, object> {
                { "phase", "close_saved_draft" },
                { "host_identity_verified", createdDraftIds.Count == 1 }
            });
            string state = TextField(closed, "state");
            if (state == "draft_saved") break;
            if (state != "draft_not_saved" && state != "draft_identity_unavailable")
                throw new InvalidOperationException("mutation_outcome_unknown");
            await Task.Delay(250);
        }
        if (TextField(closed, "state") != "draft_saved")
            throw new InvalidOperationException("mutation_outcome_unknown");
        var saved = Field(closed, "draft") as Dictionary<string, object>;
        if (saved == null || TextField(saved, "body_text") != body ||
            TextField(saved, "subject") != TextField(actual, "subject"))
            throw new InvalidOperationException("mutation_outcome_unknown");
        if (createdDraftIds.Count != 1)
            throw new InvalidOperationException("mutation_outcome_unknown");
        string observedId = createdDraftIds.Single();
        string domId = TextField(saved, "entry_id");
        if (domId.Length > 0 && domId != observedId)
            throw new InvalidOperationException("mutation_outcome_unknown");
        saved["entry_id"] = observedId;
        var reference = DraftReference(saved);
        await RequireAuthenticated();
        var result = new Dictionary<string, object> {
            { "ref", reference }, { "recipients", recipients },
            { "subject", TextField(actual, "subject") }, { "body_text", body },
            { "sending_account", expectedAccount }, { "digest", digest }
        };
        if (reply) result["source"] = source["ref"];
        return result;
    }

    async Task<Dictionary<string, object>> CreateMailDraft(Dictionary<string, object> args)
    {
        string recipient = TextField(args, "recipient_email").Trim().ToLowerInvariant();
        string subject = TextField(args, "subject");
        string body = TextField(args, "body_text");
        if (recipient != expectedAccount || subject.Length < 1 || subject.Length > 250 ||
            subject.Contains("\r") || subject.Contains("\n") || subject.Contains("\0") ||
            body.Length < 1 || body.Length > 20000 || body.Contains("\0"))
            throw new InvalidOperationException("invalid_request");
        createdDraftIds.Clear();
        mutationStarted = true;
        var created = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "new_mail" }
        });
        if (TextField(created, "state") != "composer_opening")
        {
            mutationStarted = false;
            throw new InvalidOperationException(TextField(created, "state"));
        }
        await WaitForComposer(8);
        var filled = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "fill_draft" }, { "recipient_email", recipient },
            { "subject", subject }, { "body_text", body }
        });
        if (TextField(filled, "state") != "draft_filled")
            throw new InvalidOperationException(TextField(filled, "state"));
        var recipients = await SelectRecipients(recipient);
        if (recipients.Count != 1 ||
            TextField(recipients[0], "smtp_address") != expectedAccount)
            throw new InvalidOperationException("unresolved_recipient");
        return await FinishDraft(subject, body, recipients, false, null);
    }

    async Task<Dictionary<string, object>> CreateReplyDraft(Dictionary<string, object> args)
    {
        var sourceRef = RequireReference(Field(args, "source"));
        if (String.IsNullOrWhiteSpace(TextField(sourceRef, "entry_id")))
            throw new InvalidOperationException("invalid_request");
        await RefreshMailbox();
        var source = await GetMessage(new Dictionary<string, object> {
            { "message", Field(args, "source") }
        });
        var expected = Field(args, "expected_source") as Dictionary<string, object>;
        var actualVersion = Field(source, "version") as Dictionary<string, object>;
        if (!VersionMatches(actualVersion, expected))
            throw new InvalidOperationException("stale_message");
        if (!BoolField(actualVersion, "content_complete"))
            throw new InvalidOperationException("version_incomplete");
        string body = TextField(args, "body_text");
        if (body.Length < 1 || body.Length > 20000 || body.Contains("\0"))
            throw new InvalidOperationException("invalid_request");
        createdDraftIds.Clear();
        string messageId = TextField(Field(source, "ref") as Dictionary<string, object>, "entry_id");
        bool replyAll = BoolField(args, "reply_all");
        mutationStarted = true;
        var opened = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "reply" }, { "message_id", messageId }, { "reply_all", replyAll },
            { "conversation_id", TextField(sourceRef, "conversation_id") }
        });
        if (TextField(opened, "state") != "composer_opening")
        {
            mutationStarted = false;
            throw new InvalidOperationException(TextField(opened, "state"));
        }
        await WaitForComposer(8);
        var before = await WaitForComposer(2);
        var expectedRecipients = RecipientRows(Field(before, "recipients"));
        if (expectedRecipients.Count == 0)
            throw new InvalidOperationException("unresolved_recipient");
        var filled = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "fill_draft" }, { "recipient_email", null },
            { "subject", null }, { "body_text", body }
        });
        if (TextField(filled, "state") != "draft_filled")
            throw new InvalidOperationException("mutation_outcome_unknown");
        var after = await WaitForComposer(3);
        var actualRecipients = RecipientRows(Field(after, "recipients"));
        if (!String.Equals(Json.Serialize(expectedRecipients), Json.Serialize(actualRecipients),
                StringComparison.Ordinal))
            throw new InvalidOperationException("mutation_outcome_unknown");
        return await FinishDraft(
            TextField(after, "subject"), body, expectedRecipients, true, source);
    }

    Dictionary<string, object> Summary(Dictionary<string, object> row)
    {
        string conversation = ConversationId(row);
        string subject = TextField(row, "subject");
        string receivedAt = TextField(row, "received_at");
        DateTimeOffset parsed;
        if (String.IsNullOrWhiteSpace(subject) || subject.Length > 1000 ||
            !DateTimeOffset.TryParse(receivedAt, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out parsed))
            throw new InvalidOperationException("mailbox_dom_unrecognized");
        var sender = Field(row, "sender") as Dictionary<string, object>;
        if (sender == null || String.IsNullOrWhiteSpace(TextField(sender, "display_name")))
            throw new InvalidOperationException("mailbox_dom_unrecognized");
        var lines = Field(row, "lines") as System.Collections.IList;
        string rowContent = lines == null ? TextField(row, "aria_label") :
            String.Join("\n", lines.Cast<object>().Select(Convert.ToString).ToArray());
        string senderName = TextField(sender, "display_name");
        string senderAddress = TextField(sender, "smtp_address");
        bool unread = BoolField(row, "unread");
        bool flagged = BoolField(row, "flagged");
        string importance = TextField(row, "importance");
        if (importance != "low" && importance != "normal" && importance != "high")
            throw new InvalidOperationException("mailbox_dom_unrecognized");
        string digest = DigestValues(conversation, subject, senderName, senderAddress,
            receivedAt, unread.ToString(), flagged.ToString(), importance, rowContent);
        var version = new Dictionary<string, object> {
            { "modified_at", null }, { "version_digest", digest },
            { "content_complete", false },
            { "subject", subject }, { "unread", unread },
            { "folder_entry_id", "inbox" }, { "flagged", flagged },
            { "importance", importance }
        };
        var summary = new Dictionary<string, object> {
            { "ref", new Dictionary<string, object> {
                { "entry_id", null }, { "conversation_id", conversation },
                { "store_id", storeIdentity } } },
            { "version", version }, { "sender", sender }, { "subject", subject },
            { "received_at", parsed.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture) },
            { "unread", unread }, { "flagged", flagged },
            { "importance", importance }
        };
        return summary;
    }

    async Task<InboxScanResult> ScanInbox(string folder = "Inbox")
    {
        SetStage("inbox_scan");
        var rowsById = new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
        string step = "start";
        bool restore = false;
        bool reachedBottom = false;
        bool sawScrollContainer = false;
        int stableBottoms = 0;
        int unchangedSteps = 0;
        int steps = 0;
        DateTime deadline = DateTime.UtcNow.AddSeconds(20);
        Exception failure = null;
        try
        {
            while (DateTime.UtcNow < deadline && rowsById.Count < 500 && steps < 50)
            {
                steps++;
                SetStage("inbox_scan_script");
                var result = await ExecuteMailScript(new Dictionary<string, object> {
                    { "phase", "scan" }, { "step", step }, { "folder", folder }
                });
                string state = TextField(result, "state");
                if (state == "navigating_inbox")
                {
                    await Task.Delay(250);
                    step = "start";
                    continue;
                }
                if (state != "listed")
                    throw new InvalidOperationException(state == "draft_exists"
                        ? "draft_exists" : state == "inbox_ambiguous"
                            ? "not_ready" : "mailbox_dom_unrecognized");
                if (!BoolField(result, "inbox_selected"))
                {
                    await Task.Delay(200);
                    step = "start";
                    continue;
                }
                restore = BoolField(result, "has_scroll_container");
                sawScrollContainer = sawScrollContainer || restore;
                SetStage("inbox_scan_rows");
                var rows = Field(result, "rows") as System.Collections.IList;
                if (rows == null)
                    throw new InvalidOperationException("mailbox_dom_unrecognized");
                int before = rowsById.Count;
                foreach (object rawRow in rows)
                {
                    var row = rawRow as Dictionary<string, object>;
                    if (row == null) continue;
                    string id = ConversationId(row);
                    if (!rowsById.ContainsKey(id))
                        rowsById.Add(id, row);
                }
                unchangedSteps = before == rowsById.Count ? unchangedSteps + 1 : 0;
                bool more = BoolField(result, "has_scroll_container") &&
                    !BoolField(result, "at_bottom");
                reachedBottom = BoolField(result, "at_bottom");
                if (!more)
                {
                    if (reachedBottom && unchangedSteps > 0)
                        stableBottoms++;
                    else
                        stableBottoms = 0;
                    if (!BoolField(result, "has_scroll_container"))
                    {
                        reachedBottom = true;
                        break;
                    }
                    if (stableBottoms >= 2)
                        break;
                }
                else if (unchangedSteps >= 3)
                    break;
                else if (more && step == "next" &&
                    result.ContainsKey("scroll_advanced") &&
                    !BoolField(result, "scroll_advanced"))
                    break;
                step = "next";
                await Task.Delay(150);
            }
        }
        catch (Exception exception)
        {
            failure = exception;
        }
        if (restore)
        {
            try
            {
                SetStage("inbox_restore_scroll");
                await ExecuteMailScript(new Dictionary<string, object> {
                    { "phase", "scan" }, { "step", "restore" }, { "folder", folder }
                });
            }
            catch { }
        }
        if (failure != null) throw failure;
        if (rowsById.Count == 0)
            throw new InvalidOperationException("not_ready");
        return new InboxScanResult {
            Rows = rowsById.Values.ToList(),
            Truncated = !reachedBottom || !sawScrollContainer || rowsById.Count >= 500
        };
    }

    async Task<Dictionary<string, object>> InboxList(
        Dictionary<string, object> args, bool search)
    {
        int limit = Convert.ToInt32(Field(args, "limit"));
        if (limit < 1 || limit > 50)
            throw new InvalidOperationException("invalid_request");
        var scan = await ScanInbox();
        var rawRows = scan.Rows;
        var matched = new List<Dictionary<string, object>>();
        var query = Field(args, "query") as Dictionary<string, object>;
        string subjectFilter = query == null ? "" : TextField(query, "subject_contains");
        string senderFilter = query == null ? "" : TextField(query, "sender_contains");
        string afterFilter = query == null ? "" : TextField(query, "received_after");
        if (!search)
        {
            subjectFilter = "";
            senderFilter = "";
            afterFilter = "";
        }
        bool unreadOnly = BoolField(args, "unread_only") ||
            query != null && BoolField(query, "unread_only");
        if (!search && query != null)
            unreadOnly = BoolField(args, "unread_only");
        bool filterAfter = !String.IsNullOrEmpty(afterFilter);
        DateTimeOffset receivedAfter = DateTimeOffset.MinValue;
        if (filterAfter &&
            !DateTimeOffset.TryParse(afterFilter, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out receivedAfter))
            throw new InvalidOperationException("invalid_request");

        foreach (var row in rawRows)
        {
            var summary = Summary(row);
            string subject = TextField(summary, "subject");
            var sender = Field(summary, "sender") as Dictionary<string, object>;
            string senderText = TextField(sender, "display_name") + " " +
                TextField(sender, "smtp_address");
            DateTimeOffset received = DateTimeOffset.Parse(
                TextField(summary, "received_at"), CultureInfo.InvariantCulture);
            if (unreadOnly && !BoolField(summary, "unread")) continue;
            if (!String.IsNullOrEmpty(subjectFilter) &&
                subject.IndexOf(subjectFilter, StringComparison.OrdinalIgnoreCase) < 0) continue;
            if (!String.IsNullOrEmpty(senderFilter) &&
                senderText.IndexOf(senderFilter, StringComparison.OrdinalIgnoreCase) < 0) continue;
            if (filterAfter && received <= receivedAfter) continue;
            matched.Add(summary);
            if (matched.Count >= 500) break;
        }
        bool truncated = matched.Count > limit || scan.Truncated;
        return new Dictionary<string, object> {
            { "items", matched.Take(limit).ToList() },
            { "truncated", truncated }, { "scanned_count", rawRows.Count }
        };
    }

    async Task OpenConversation(string conversation, string folder = "Inbox")
    {
        SetStage("message_open");
        for (int step = 0; step < 50; step++)
        {
            var opened = await ExecuteMailScript(new Dictionary<string, object> {
                { "phase", "open" }, { "conversation_id", conversation }
            });
            string state = TextField(opened, "state");
            if (state == "opening") return;
            if (state != "not_visible")
                throw new InvalidOperationException(state);
            var scroll = await ExecuteMailScript(new Dictionary<string, object> {
                { "phase", "scan" }, { "step", "next" }, { "folder", folder }
            });
            if (TextField(scroll, "state") != "listed" ||
                !BoolField(scroll, "inbox_selected"))
                throw new InvalidOperationException("not_ready");
            if (BoolField(scroll, "at_bottom") &&
                !BoolField(scroll, "scroll_advanced"))
                throw new InvalidOperationException("message_not_found");
            await Task.Delay(150);
        }
        throw new InvalidOperationException("message_not_found");
    }

    async Task<Dictionary<string, object>> GetMessage(Dictionary<string, object> args)
    {
        SetStage("message_reference");
        var reference = RequireReference(Field(args, "message"));
        string conversation = TextField(reference, "conversation_id");
        string requestedMessage = TextField(reference, "entry_id");
        string folder = TextField(args, "folder");
        if (String.IsNullOrEmpty(folder)) folder = "Inbox";
        if (folder != "Inbox" && folder != "Archive" && folder != "Sent Items")
            throw new InvalidOperationException("invalid_request");
        if (String.IsNullOrWhiteSpace(conversation))
            throw new InvalidOperationException("message_not_found");
        SetStage("message_inbox_scan");
        var visibleRows = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "scan" }, { "step", "start" }, { "folder", folder }
        });
        var loaded = Field(visibleRows, "rows") as System.Collections.IList;
        bool visibleMatch = TextField(visibleRows, "state") == "listed" &&
            BoolField(visibleRows, "inbox_selected") && loaded != null &&
            loaded.Cast<object>().Count(row => row is Dictionary<string, object> &&
                ConversationId((Dictionary<string, object>)row) == conversation) == 1;
        var scan = visibleMatch ? new InboxScanResult {
            Rows = loaded.Cast<Dictionary<string, object>>().ToList(), Truncated = true
        } : await ScanInbox(folder);
        SetStage("message_conversation_match");
        if (scan.Rows.Count(row => ConversationId(row) == conversation) != 1)
            throw new InvalidOperationException("message_not_found");
        await OpenConversation(conversation, folder);
        SetStage("message_dom_read");
        DateTime deadline = DateTime.UtcNow.AddSeconds(12);
        Dictionary<string, object> result = null;
        while (DateTime.UtcNow < deadline)
        {
            result = await ExecuteMailScript(new Dictionary<string, object> {
                { "phase", "read" }, { "conversation_id", conversation },
                { "message_id", String.IsNullOrEmpty(requestedMessage) ? null : requestedMessage }
            });
            string state = TextField(result, "state");
            if (state == "read" && observedConversations.ContainsKey(conversation)) break;
            if (state == "message_identity_ambiguous")
                throw new InvalidOperationException(state);
            if (state != "waiting" && state != "read")
                throw new InvalidOperationException(state);
            await Task.Delay(200);
        }
        if (result == null || TextField(result, "state") != "read")
            throw new InvalidOperationException("not_ready");
        SetStage("message_identity_verify");
        await RequireAuthenticated();
        // The exact message is the single non-draft item Outlook itself returned for
        // the selected conversation. Anything else stops without a guess.
        List<Dictionary<string, object>> observed;
        if (!observedConversations.TryGetValue(conversation, out observed))
            throw new InvalidOperationException("message_identity_unavailable");
        var exact = observed.Where(item => BoolField(item, "draft_known") &&
            !BoolField(item, "is_draft")).ToList();
        if (exact.Count != 1)
            throw new InvalidOperationException(exact.Count == 0
                ? "message_identity_unavailable" : "message_identity_ambiguous");
        var record = exact[0];
        var messageRow = Field(result, "item") as Dictionary<string, object>;
        string messageId = TextField(record, "entry_id");
        string domMessage = TextField(result, "dom_message_id");
        if (!String.IsNullOrEmpty(domMessage) && domMessage != messageId)
            throw new InvalidOperationException("message_identity_ambiguous");
        if (messageRow == null || ConversationId(messageRow) != conversation ||
            !String.IsNullOrEmpty(requestedMessage) && messageId != requestedMessage)
            throw new InvalidOperationException("stale_message");
        SetStage("message_version_build");
        var detail = Summary(messageRow);
        string body = TextField(result, "body_text");
        var recipients = Field(record, "recipients") as List<Dictionary<string, object>>;
        bool truncated = BoolField(result, "body_truncated") || body.Length > 20000;
        if (body.Length > 20000) body = body.Substring(0, 20000);
        string addresses = String.Join("\n", recipients.Select(recipient =>
            Convert.ToString(recipient["recipient_type"]) + ":" +
            Convert.ToString(recipient["smtp_address"])).ToArray());
        var version = Field(detail, "version") as Dictionary<string, object>;
        version["folder_entry_id"] = folder == "Inbox" ? "inbox" :
            folder == "Archive" ? "archive" : "sent";
        version["version_digest"] = DigestValues(conversation, messageId,
            TextField(record, "change_key"),
            TextField(detail, "subject"), TextField(Field(detail, "sender") as Dictionary<string, object>,
                "display_name"), body, TextField(detail, "received_at"),
            BoolField(detail, "unread").ToString(), BoolField(detail, "flagged").ToString(),
            TextField(detail, "importance"), addresses,
            BoolField(record, "has_attachments").ToString());
        // Complete only when Outlook's response supplied the item version, every
        // recipient, and the attachment and draft flags, and the body is whole.
        version["content_complete"] = BoolField(record, "complete") && !truncated;
        detail["ref"] = new Dictionary<string, object> {
            { "entry_id", messageId }, { "conversation_id", conversation },
            { "store_id", storeIdentity }
        };
        detail["recipients"] = recipients;
        var sender = Field(record, "sender") as Dictionary<string, object>;
        if (sender != null) detail["sender"] = sender;
        detail["body_text"] = body;
        detail["body_truncated"] = truncated;
        var capability = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "reply_capability" }, { "conversation_id", conversation }
        });
        detail["can_create_reply_draft"] = BoolField(version, "content_complete") &&
            BoolField(capability, "available");
        return detail;
    }

    static bool VersionMatches(
        Dictionary<string, object> current, Dictionary<string, object> expected)
    {
        if (current == null || expected == null) return false;
        return BoolField(current, "content_complete") &&
            BoolField(expected, "content_complete") &&
            Regex.IsMatch(TextField(current, "version_digest"), "^[0-9a-f]{64}$") &&
            TextField(current, "version_digest") == TextField(expected, "version_digest") &&
            TextField(current, "subject") == TextField(expected, "subject") &&
            BoolField(current, "unread") == BoolField(expected, "unread") &&
            TextField(current, "folder_entry_id") == TextField(expected, "folder_entry_id") &&
            BoolField(current, "flagged") == BoolField(expected, "flagged") &&
            TextField(current, "importance") == TextField(expected, "importance");
    }

    async Task<Dictionary<string, object>> OpenDraft(Dictionary<string, object> reference)
    {
        string id = TextField(reference, "entry_id");
        if (id.Length == 0) throw new InvalidOperationException("stale_draft");
        var scan = await ScanInbox("Drafts");
        Dictionary<string, object> record;
        if (!observedMessages.TryGetValue(id, out record) || !BoolField(record, "is_draft"))
            throw new InvalidOperationException("stale_draft");
        string conversation = TextField(record, "conversation_id");
        var matches = scan.Rows.Where(row => ConversationId(row) == conversation ||
            ConversationId(row) == id).ToList();
        if (matches.Count != 1) throw new InvalidOperationException("stale_draft");
        await OpenConversation(ConversationId(matches[0]), "Drafts");
        var composer = await WaitForComposer(10);
        await RequireAuthenticated();
        if (!observedMessages.TryGetValue(id, out record) ||
            !BoolField(record, "is_draft") || BoolField(record, "has_attachments"))
            throw new InvalidOperationException("stale_draft");
        var sender = Field(record, "sender") as Dictionary<string, object>;
        if (sender == null || TextField(sender, "smtp_address") != expectedAccount)
            throw new InvalidOperationException("stale_draft");
        string domId = TextField(composer, "entry_id");
        if (domId.Length > 0 && domId != id) throw new InvalidOperationException("stale_draft");
        return composer;
    }

    async Task<Dictionary<string, object>> SendDraft(Dictionary<string, object> args)
    {
        var reference = RequireReference(Field(args, "draft"));
        string expectedDigest = TextField(args, "expected_digest");
        if (!Regex.IsMatch(expectedDigest, "^[0-9a-f]{64}$"))
            throw new InvalidOperationException("invalid_request");
        await RefreshMailbox();
        await ScanInbox("Sent Items");
        var previous = new HashSet<string>(observedMessages.Keys, StringComparer.Ordinal);
        var composer = await OpenDraft(reference);
        var recipients = RecipientRows(Field(composer, "recipients"));
        string subject = TextField(composer, "subject");
        string body = TextField(composer, "body_text");
        if (subject.Length == 0 || subject.Length > 250 || body.Length > 20000 ||
            Convert.ToInt32(Field(composer, "attachment_count")) != 0 ||
            recipients.Count == 0 || DraftDigest(subject, body, recipients) != expectedDigest)
            throw new InvalidOperationException("stale_draft");
        await RequireAuthenticated();
        DateTimeOffset started = DateTimeOffset.UtcNow;
        SetStage("send_click");
        mutationStarted = true;
        var clicked = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "send" }, { "subject", subject }, { "body_text", body },
            { "recipients", Field(composer, "recipients") }
        });
        if (TextField(clicked, "state") != "send_clicked")
            throw new InvalidOperationException("mutation_outcome_unknown");
        SetStage("sent_items_verify");
        await Task.Delay(1000);
        // After the click every failure remains uncertain. Never click again.
        await ScanInbox("Sent Items");
        var candidates = observedMessages.Values.Where(record =>
        {
            DateTimeOffset sent;
            var sender = Field(record, "sender") as Dictionary<string, object>;
            return !previous.Contains(TextField(record, "entry_id")) &&
                !BoolField(record, "is_draft") && TextField(record, "subject") == subject &&
                sender != null && TextField(sender, "smtp_address") == expectedAccount &&
                RecipientKey(RecipientRows(Field(record, "recipients"))) == RecipientKey(recipients) &&
                DateTimeOffset.TryParse(TextField(record, "sent_at"), out sent) &&
                sent >= started.AddSeconds(-5);
        }).ToList();
        if (candidates.Count != 1) throw new InvalidOperationException("mutation_outcome_unknown");
        var candidate = candidates[0];
        var sentRef = new Dictionary<string, object> {
            { "entry_id", TextField(candidate, "entry_id") },
            { "conversation_id", TextField(candidate, "conversation_id") },
            { "store_id", storeIdentity }
        };
        var sentDetail = await GetMessage(new Dictionary<string, object> {
            { "message", sentRef }, { "folder", "Sent Items" }
        });
        if (TextField(sentDetail, "body_text") != body || BoolField(sentDetail, "body_truncated"))
            throw new InvalidOperationException("mutation_outcome_unknown");
        await RequireAuthenticated();
        return new Dictionary<string, object> {
            { "state", "sent_verified" }, { "draft", reference },
            { "account_verified", true }, { "sent_items_verified", true },
            { "submitted_at", DateTimeOffset.UtcNow.ToString("o") },
            { "subject", subject }, { "body_text", body }, { "body_truncated", false }
        };
    }

    async Task<Dictionary<string, object>> Archive(Dictionary<string, object> args)
    {
        var reference = RequireReference(Field(args, "message"));
        await RefreshMailbox();
        var detail = await GetMessage(new Dictionary<string, object> { { "message", reference } });
        var version = Field(detail, "version") as Dictionary<string, object>;
        if (!VersionMatches(version, Field(args, "expected_source") as Dictionary<string, object>))
            throw new InvalidOperationException("stale_message");
        if (BoolField(detail, "flagged") || TextField(detail, "importance") == "high")
            throw new InvalidOperationException("archive_protected");
        var status = await RequireAuthenticated();
        if (Convert.ToInt32(Field(status, "archive_folder_control_count")) != 1)
            throw new InvalidOperationException("archive_unavailable");
        movedItemIds.Clear();
        SetStage("archive_click");
        mutationStarted = true;
        var clicked = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "archive" }, { "conversation_id", TextField(reference, "conversation_id") }
        });
        if (TextField(clicked, "state") != "archive_clicked")
            throw new InvalidOperationException("mutation_outcome_unknown");
        await Task.Delay(1000);
        SetStage("archive_verify");
        var archivedDetail = await GetMessage(new Dictionary<string, object> {
            { "message", new Dictionary<string, object> {
                { "conversation_id", TextField(reference, "conversation_id") },
                { "store_id", storeIdentity }
            } }, { "folder", "Archive" }
        });
        var archivedRef = Field(archivedDetail, "ref") as Dictionary<string, object>;
        if (movedItemIds.Count != 1 ||
            !movedItemIds.Contains(TextField(archivedRef, "entry_id")) ||
            TextField(detail, "subject") != TextField(archivedDetail, "subject") ||
            TextField(detail, "body_text") != TextField(archivedDetail, "body_text") ||
            RecipientKey(RecipientRows(Field(detail, "recipients"))) !=
                RecipientKey(RecipientRows(Field(archivedDetail, "recipients"))))
            throw new InvalidOperationException("mutation_outcome_unknown");
        return new Dictionary<string, object> {
            { "original", reference }, { "archived", archivedRef },
            { "archived_at", DateTimeOffset.UtcNow.ToString("o") }
        };
    }

    static string SafeAttachmentName(string name, int index)
    {
        if (index < 1 || index > 500) throw new InvalidOperationException("invalid_request");
        string clean = Regex.Replace(name ?? "", "[\\x00-\\x1f<>:\"/\\\\|?*]", "_").Trim(' ', '.');
        if (clean.Length == 0) clean = "attachment";
        if (clean.Length > 120)
        {
            string extension = Path.GetExtension(clean);
            if (extension.Length > 20) extension = "";
            clean = clean.Substring(0, 120 - extension.Length) + extension;
        }
        return index.ToString("D2") + "_" + clean;
    }

    static void RequirePlainDirectory(string path)
    {
        if (Directory.Exists(path) &&
            (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("attachment_unavailable");
        Directory.CreateDirectory(path);
        var acl = new DirectorySecurity();
        acl.SetAccessRuleProtection(true, false);
        acl.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User,
            FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
            PropagationFlags.None, AccessControlType.Allow));
        acl.AddAccessRule(new FileSystemAccessRule(
            new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
            FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
            PropagationFlags.None, AccessControlType.Allow));
        new DirectoryInfo(path).SetAccessControl(acl);
    }

    async Task<Dictionary<string, object>> DownloadAttachments(Dictionary<string, object> args)
    {
        var reference = RequireReference(args);
        var detail = await GetMessage(new Dictionary<string, object> { { "message", reference } });
        Dictionary<string, object> record;
        if (!observedMessages.TryGetValue(TextField(reference, "entry_id"), out record))
            throw new InvalidOperationException("attachment_unavailable");
        var attachments = Field(record, "attachments") as List<Dictionary<string, object>>;
        if (attachments == null || BoolField(record, "has_attachments") && attachments.Count == 0)
            throw new InvalidOperationException("attachment_unavailable");
        bool pdfOnly = BoolField(args, "pdf_only");
        var selected = attachments.Where(item => !pdfOnly ||
            !BoolField(item, "inline") &&
            TextField(item, "filename").EndsWith(".pdf", StringComparison.OrdinalIgnoreCase)).ToList();
        long total = selected.Sum(item => Convert.ToInt64(Field(item, "size_bytes")));
        if (selected.Count > 25 || total > 52428800)
            throw new InvalidOperationException("attachment_limit_exceeded");
        string appRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "OfficeActionServer");
        string root = Path.Combine(appRoot, "outlook-web-attachments");
        RequirePlainDirectory(appRoot);
        RequirePlainDirectory(root);
        string folder = Path.Combine(root, DigestValues(TextField(reference, "entry_id"), storeIdentity));
        RequirePlainDirectory(folder);
        var result = new List<Dictionary<string, object>>();
        long savedTotal = 0;
        foreach (var attachment in selected)
        {
            int index = Convert.ToInt32(Field(attachment, "index"));
            string name = TextField(attachment, "filename");
            string temp = Path.Combine(folder, Guid.NewGuid().ToString("N") + ".tmp");
            pendingDownload = null;
            pendingDownloadPath = temp;
            allowDownload = true;
            try
            {
                var downloadRequest = new Dictionary<string, object> {
                    { "phase", "download_attachment" },
                    { "conversation_id", TextField(reference, "conversation_id") },
                    { "attachment_id", TextField(attachment, "attachment_id") }, { "filename", name }
                };
                var clicked = await ExecuteMailScript(downloadRequest);
                if (TextField(clicked, "state") == "attachment_menu_opening")
                {
                    await Task.Delay(300);
                    clicked = await ExecuteMailScript(downloadRequest);
                }
                if (TextField(clicked, "state") != "download_clicked")
                    throw new InvalidOperationException("attachment_unavailable");
                DateTime deadline = DateTime.UtcNow.AddSeconds(30);
                while (DateTime.UtcNow < deadline &&
                    (pendingDownload == null || pendingDownload.State == CoreWebView2DownloadState.InProgress))
                {
                    if (pendingDownload != null && pendingDownload.BytesReceived + savedTotal > 52428800)
                    {
                        pendingDownload.Cancel();
                        throw new InvalidOperationException("attachment_limit_exceeded");
                    }
                    await Task.Delay(100);
                }
                if (pendingDownload == null ||
                    pendingDownload.State != CoreWebView2DownloadState.Completed || !File.Exists(temp))
                    throw new InvalidOperationException("attachment_unavailable");
                long size = new FileInfo(temp).Length;
                if (size + savedTotal > 52428800 ||
                    size != Convert.ToInt64(Field(attachment, "size_bytes")))
                    throw new InvalidOperationException("attachment_limit_exceeded");
                string hash;
                using (var sha = System.Security.Cryptography.SHA256.Create())
                using (var stream = File.OpenRead(temp))
                    hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                string destination = Path.Combine(folder, hash + "_" + SafeAttachmentName(name, index));
                if (File.Exists(destination))
                {
                    if ((File.GetAttributes(destination) & FileAttributes.ReparsePoint) != 0)
                        throw new InvalidOperationException("attachment_unavailable");
                    using (var sha = System.Security.Cryptography.SHA256.Create())
                    using (var stream = File.OpenRead(destination))
                        if (BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant() != hash)
                            throw new InvalidOperationException("attachment_unavailable");
                }
                else File.Move(temp, destination);
                savedTotal += size;
                result.Add(new Dictionary<string, object> {
                    { "index", index }, { "filename", name }, { "size_bytes", size },
                    { "local_path", destination }, { "sha256", hash }
                });
            }
            finally
            {
                allowDownload = false;
                if (pendingDownload != null && pendingDownload.State == CoreWebView2DownloadState.InProgress)
                    pendingDownload.Cancel();
                pendingDownload = null;
                pendingDownloadPath = "";
                if (File.Exists(temp)) File.Delete(temp);
            }
        }
        return new Dictionary<string, object> { { "ref", Field(detail, "ref") }, { "items", result } };
    }

    async Task<Dictionary<string, object>> Dispatch(Dictionary<string, object> request)
    {
        var allowedOperations = new HashSet<string>(StringComparer.Ordinal) {
            "outlook_status", "shutdown", "outlook_internal_identity_probe",
            "outlook_internal_response_probe",
            "outlook_inbox_list", "outlook_search_inbox", "outlook_get_message",
            "outlook_create_reply_draft", "outlook_create_mail_draft", "outlook_send_draft",
            "outlook_archive", "outlook_download_attachments"
        };
        string requestedOperation = request.ContainsKey("operation")
            ? Convert.ToString(request["operation"]) : "";
        activeOperation = allowedOperations.Contains(requestedOperation)
            ? requestedOperation : "invalid_request";
        mutationStarted = false;
        SetStage("dispatch");
        if (!request.ContainsKey("version") || Convert.ToInt32(request["version"]) != 1)
            throw new InvalidOperationException("invalid_request");
        string operation = Convert.ToString(request["operation"]);
        var args = request.ContainsKey("arguments")
            ? request["arguments"] as Dictionary<string, object> : null;
        if (args == null) throw new InvalidOperationException("invalid_request");
        if (operation == "outlook_status") return await Inspect();
        if (operation == "shutdown")
        {
            object token;
            if (!args.TryGetValue("token", out token) ||
                !String.Equals(Convert.ToString(token),
                    Environment.GetEnvironmentVariable("OFFICE_OUTLOOK_SHUTDOWN_TOKEN"),
                    StringComparison.Ordinal))
                throw new InvalidOperationException("invalid_request");
            return new Dictionary<string, object> { { "stopped", true } };
        }
        if (operation.StartsWith("outlook_", StringComparison.Ordinal))
        {
            SetStage("identity_preflight");
            await RequireAuthenticated();
        }
        SetStage("operation_dispatch");
        if (operation == "outlook_internal_identity_probe")
            return await ExecuteMailScript(new Dictionary<string, object> {
                { "phase", "identity_probe" }, { "open_details", BoolField(args, "open_details") }
            });
        if (operation == "outlook_internal_response_probe")
        {
            string mode = TextField(args, "mode");
            if (mode == "arm")
            {
                var selected = await ExecuteMailScript(new Dictionary<string, object> {
                    { "phase", "selected_conversation" }
                });
                string conversation = TextField(selected, "conversation_id");
                if (TextField(selected, "state") != "selected" ||
                    String.IsNullOrWhiteSpace(conversation) || conversation.Length > 4096)
                    throw new InvalidOperationException("message_identity_unavailable");
                responseProbeUntil = DateTime.MinValue;
                responseProbeConversation = conversation;
                responseProbeMatched = responseProbeGetConversationItems = 0;
                responseProbeJson = responseProbeOversize = responseProbeItemIds = 0;
                responseProbeReadErrors = responseProbeEmpty = 0;
                responseProbeConversationIds = responseProbeConversationMatches = 0;
                responseProbeChangeKeys = responseProbeBodies = 0;
                responseProbeUntil = DateTime.UtcNow.AddMinutes(2);
            }
            else if (mode == "disarm")
            {
                responseProbeUntil = DateTime.MinValue;
                responseProbeConversation = "";
            }
            else if (mode == "reload")
            {
                controller.CoreWebView2.Reload();
            }
            else if (mode != "inspect") throw new InvalidOperationException("invalid_request");
            return ResponseProbeStatus();
        }
        if (operation == "outlook_inbox_list")
            return await InboxList(args, false);
        if (operation == "outlook_search_inbox")
            return await InboxList(args, true);
        if (operation == "outlook_get_message")
            return await GetMessage(args);
        if (operation == "outlook_create_reply_draft")
            return await CreateReplyDraft(args);
        if (operation == "outlook_create_mail_draft")
            return await CreateMailDraft(args);
        if (operation == "outlook_send_draft")
            return await SendDraft(args);
        if (operation == "outlook_archive")
            return await Archive(args);
        if (operation == "outlook_download_attachments")
            return await DownloadAttachments(args);
        throw new InvalidOperationException("not_implemented");
    }

    Task<Dictionary<string, object>> OnSta(Dictionary<string, object> request)
    {
        var done = new TaskCompletionSource<Dictionary<string, object>>();
        host.BeginInvoke(new Action(async () =>
        {
            try { done.SetResult(await Dispatch(request)); }
            catch (Exception exception)
            {
                lastFailureOperation = activeOperation;
                lastFailureStage = activeStage;
                lastFailureType = exception.GetType().Name;
                lastFailureCode = mutationStarted ? "mutation_outcome_unknown" :
                    SafeErrorCode(exception.Message);
                done.SetException(new InvalidOperationException(lastFailureCode));
            }
        }));
        return done.Task;
    }

    static async Task<byte[]> ReadExact(Stream stream, int length, CancellationToken token)
    {
        byte[] data = new byte[length];
        int offset = 0;
        while (offset < length)
        {
            int count = await stream.ReadAsync(data, offset, length - offset, token);
            if (count == 0) throw new EndOfStreamException();
            offset += count;
        }
        return data;
    }

    async Task Serve()
    {
        var acl = new PipeSecurity();
        acl.SetAccessRuleProtection(true, false);
        acl.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,
            PipeAccessRights.FullControl, AccessControlType.Allow));
        acl.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
            PipeAccessRights.FullControl, AccessControlType.Allow));
        while (!stop.IsCancellationRequested)
        {
            try
            {
                using (var pipe = new NamedPipeServerStream(pipeName, PipeDirection.InOut, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous, 65536, 65536, acl))
                {
                    await pipe.WaitForConnectionAsync(stop.Token);
                    var machine = new StringBuilder(256);
                    bool named = GetNamedPipeClientComputerName(pipe.SafePipeHandle, machine, (uint)machine.Capacity);
                    if (named || Marshal.GetLastWin32Error() != 229) throw new InvalidDataException();
                    Dictionary<string, object> request;
                    using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(stop.Token))
                    {
                        deadline.CancelAfter(10000);
                        int size = BitConverter.ToInt32(await ReadExact(pipe, 4, deadline.Token), 0);
                        if (size < 2 || size > MaxFrame) throw new InvalidDataException();
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
                    catch (Exception exception)
                    {
                        string reason = SafeErrorCode(exception.Message);
                        response = new Dictionary<string, object> {
                            { "version", 1 }, { "ok", false }, { "error", reason }
                        };
                    }
                    byte[] bytes = Encoding.UTF8.GetBytes(Json.Serialize(response));
                    await pipe.WriteAsync(BitConverter.GetBytes(bytes.Length), 0, 4, stop.Token);
                    await pipe.WriteAsync(bytes, 0, bytes.Length, stop.Token);
                    await pipe.FlushAsync(stop.Token);
                    object op;
                    if (request.TryGetValue("operation", out op) &&
                        Convert.ToString(op) == "shutdown" && (bool)response["ok"])
                        host.BeginInvoke(new Action(() => ExitThread()));
                }
            }
            catch (OperationCanceledException) { if (stop.IsCancellationRequested) break; }
            catch (Exception) { Thread.Sleep(100); }
        }
    }

    void MonitorParent()
    {
        try { using (var parent = Process.GetProcessById(parentId)) parent.WaitForExit(); }
        catch { }
        try { host.BeginInvoke(new Action(() => ExitThread())); } catch { }
    }

    protected override void ExitThreadCore()
    {
        stop.Cancel();
        if (controller != null) controller.Close();
        if (profileLock != null)
        {
            try { profileLock.Unlock(0, 1); } catch { }
            profileLock.Dispose();
        }
        host.Dispose();
        base.ExitThreadCore();
    }

    public static void Run(string sdk, string profile, string pipe, string generation, int parent, bool visible)
    {
        Application.Run(new OutlookSession(sdk, profile, pipe, generation, parent, visible));
    }
}
