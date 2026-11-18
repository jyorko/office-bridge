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
    bool initializing;
    // Bounded identity metadata from Outlook's own conversation responses. No bodies.
    readonly Dictionary<string, List<Dictionary<string, object>>> observedConversations =
        new Dictionary<string, List<Dictionary<string, object>>>(StringComparer.Ordinal);
    readonly Queue<string> observedOrder = new Queue<string>();

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
            !Regex.IsMatch(uri.AbsolutePath, "^/owa/(?:[0-9]+/)?service\\.svc$",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant) ||
            uri.Query.Length > 4096)
            return "";
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
        if (actionCount != 1) return "";
        if (action.Equals("GetConversationItems", StringComparison.OrdinalIgnoreCase))
            return "GetConversationItems";
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
                foreach (var field in map)
                {
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
                    ? entry["Mailbox"] as Dictionary<string, object> : entry;
                string address = mailbox == null ? "" :
                    (TextField(mailbox, "EmailAddress") ?? "").Trim().ToLowerInvariant();
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

    // Reduce one parsed service response to per-conversation identity records.
    static Dictionary<string, List<Dictionary<string, object>>> ObservedItems(object root)
    {
        var found = new Dictionary<string, List<Dictionary<string, object>>>(StringComparer.Ordinal);
        var pending = new Stack<object>();
        pending.Push(root);
        int visited = 0, records = 0;
        while (pending.Count > 0 && visited++ < 20000 && records < 200)
        {
            object node = pending.Pop();
            var map = node as Dictionary<string, object>;
            if (map == null)
            {
                var list = node as System.Collections.IList;
                if (list != null) foreach (object child in list)
                    if (child is Dictionary<string, object> || child is System.Collections.IList)
                        pending.Push(child);
                continue;
            }
            foreach (var field in map)
                if (field.Value is Dictionary<string, object> ||
                    field.Value is System.Collections.IList) pending.Push(field.Value);
            if (!map.ContainsKey("ItemId") || !map.ContainsKey("ConversationId")) continue;
            var identity = map["ItemId"] as Dictionary<string, object>;
            string entry = IdentityText(identity);
            string changeKey = identity == null || !identity.ContainsKey("ChangeKey")
                ? "" : IdentityText(identity["ChangeKey"]);
            string conversation = IdentityText(map["ConversationId"]);
            if (entry.Length == 0 || changeKey.Length == 0 || conversation.Length == 0) continue;
            bool complete = map.ContainsKey("HasAttachments") && map["HasAttachments"] is bool &&
                map.ContainsKey("IsDraft") && map["IsDraft"] is bool;
            var recipients = ObservedRecipients(map, ref complete);
            var record = new Dictionary<string, object> {
                { "entry_id", entry }, { "change_key", changeKey },
                { "conversation_id", conversation },
                { "is_draft", map.ContainsKey("IsDraft") && map["IsDraft"] is bool &&
                    (bool)map["IsDraft"] },
                { "has_attachments", map.ContainsKey("HasAttachments") &&
                    map["HasAttachments"] is bool && (bool)map["HasAttachments"] },
                { "recipients", recipients }, { "complete", complete }
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
        return found;
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
        }
    }

    async Task ObserveMailResponse(CoreWebView2WebResourceResponseReceivedEventArgs e)
    {
        bool probing = DateTime.UtcNow < responseProbeUntil && !responseProbeBusy &&
            responseProbeMatched < 8;
        try
        {
            string action = MailResponseAction(e.Request.Uri);
            if (String.IsNullOrEmpty(action) || e.Response.StatusCode != 200) return;
            if (probing)
            {
                responseProbeBusy = true;
                responseProbeMatched++;
                responseProbeGetConversationItems++;
            }
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
                    var parser = new JavaScriptSerializer {
                        MaxJsonLength = MaxFrame, RecursionLimit = 64
                    };
                    object parsed = parser.DeserializeObject(
                        Encoding.UTF8.GetString(memory.ToArray()));
                    if (parsed == null) return;
                    var found = ObservedItems(parsed);
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
        catch (Exception) { if (probing) responseProbeReadErrors++; }
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
        };
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
        for (int attempt = 0; attempt < 8; attempt++)
        {
            status = await Inspect();
            if (status.ContainsKey("account_verified") &&
                Convert.ToBoolean(status["account_verified"]))
                return status;
            string state = status.ContainsKey("state")
                ? Convert.ToString(status["state"]) : "identity_unverified";
            if (state != "identity_unverified") break;
            await Task.Delay(400);
        }
        throw new InvalidOperationException(status != null &&
            status.ContainsKey("state") ? Convert.ToString(status["state"]) : "identity_unverified");
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
        string digest = DraftDigest(TextField(actual, "subject"), body, recipients);
        var closed = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "close_saved_draft" }
        });
        if (TextField(closed, "state") != "draft_saved")
            throw new InvalidOperationException("mutation_outcome_unknown");
        var saved = Field(closed, "draft") as Dictionary<string, object>;
        if (saved == null || TextField(saved, "body_text") != body ||
            TextField(saved, "subject") != TextField(actual, "subject"))
            throw new InvalidOperationException("mutation_outcome_unknown");
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
        var created = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "new_mail" }
        });
        if (TextField(created, "state") != "composer_opening")
            throw new InvalidOperationException(TextField(created, "state"));
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
        string messageId = TextField(Field(source, "ref") as Dictionary<string, object>, "entry_id");
        bool replyAll = BoolField(args, "reply_all");
        var opened = await ExecuteMailScript(new Dictionary<string, object> {
            { "phase", "reply" }, { "message_id", messageId }, { "reply_all", replyAll }
        });
        if (TextField(opened, "state") != "composer_opening")
            throw new InvalidOperationException(TextField(opened, "state"));
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

    async Task<InboxScanResult> ScanInbox()
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
                    { "phase", "scan" }, { "step", step }
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
                    { "phase", "scan" }, { "step", "restore" }
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

    async Task OpenConversation(string conversation)
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
                { "phase", "scan" }, { "step", "next" }
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
        if (String.IsNullOrWhiteSpace(conversation))
            throw new InvalidOperationException("message_not_found");
        SetStage("message_inbox_scan");
        var scan = await ScanInbox();
        SetStage("message_conversation_match");
        if (scan.Rows.Count(row => ConversationId(row) == conversation) != 1)
            throw new InvalidOperationException("message_not_found");
        await OpenConversation(conversation);
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
        var exact = observed.Where(item => !BoolField(item, "is_draft")).ToList();
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
        detail["body_text"] = body;
        detail["body_truncated"] = truncated;
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

    async Task<Dictionary<string, object>> Dispatch(Dictionary<string, object> request)
    {
        var allowedOperations = new HashSet<string>(StringComparer.Ordinal) {
            "outlook_status", "shutdown", "outlook_internal_identity_probe",
            "outlook_internal_response_probe",
            "outlook_inbox_list", "outlook_search_inbox", "outlook_get_message"
        };
        string requestedOperation = request.ContainsKey("operation")
            ? Convert.ToString(request["operation"]) : "";
        activeOperation = allowedOperations.Contains(requestedOperation)
            ? requestedOperation : "invalid_request";
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
            else if (mode != "inspect") throw new InvalidOperationException("invalid_request");
            return ResponseProbeStatus();
        }
        if (operation == "outlook_inbox_list")
            return await InboxList(args, false);
        if (operation == "outlook_search_inbox")
            return await InboxList(args, true);
        if (operation == "outlook_get_message")
            return await GetMessage(args);
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
                lastFailureCode = SafeErrorCode(exception.Message);
                done.SetException(exception);
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
