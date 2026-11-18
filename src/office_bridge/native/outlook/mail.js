(() => {
  const request = __REQUEST__;
  const visible = (element) =>
    !!element &&
    !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
  const cleanText = (element) =>
    (element && (element.innerText || element.textContent) || "")
      .replace(/\u00a0/g, " ")
      .split(/\r?\n/)
      .map((part) => part.replace(/[\uE000-\uF8FF]/g, "").trim())
      .filter(Boolean);
  const rowId = (row) =>
    row.getAttribute("data-itemid") ||
    row.getAttribute("data-item-id") ||
    row.getAttribute("data-convid") ||
    "";
  const messageRows = () =>
    Array.from(
      document.querySelectorAll(
        '[role="option"][data-itemid],[role="option"][data-item-id],' +
          '[role="option"][data-convid]',
      ),
    ).filter(visible);
  const treeItems = () =>
    Array.from(document.querySelectorAll('[role="treeitem"]')).filter(visible);
  const folderLabel = (element) =>
    (
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      cleanText(element).join(" ")
    )
      .trim()
      .replace(/\s+/g, " ");
  const folderItems = (name) =>
    treeItems().filter((element) => new RegExp(`^${name}\\b`, "i").test(folderLabel(element)));
  const selectedFolder = (element) =>
    element.getAttribute("aria-selected") === "true" ||
    element.getAttribute("aria-current") === "page" ||
    element.getAttribute("aria-checked") === "true" ||
    /(^|\s)(selected|is-selected)(\s|$)/i.test(String(element.className || "")) ||
    Array.from(
      element.querySelectorAll(
        '[aria-selected="true"],[aria-current="page"],[aria-checked="true"],' +
          "[data-is-selected=true]",
      ),
    ).some(visible) ||
    Array.from(element.querySelectorAll("*")).some((child) =>
      /(^|\s)(selected|is-selected)(\s|$)/i.test(String(child.className || "")),
    );
  const folderName = ["Inbox", "Drafts", "Sent Items", "Archive"].includes(request.folder)
    ? request.folder : "Inbox";
  const folderState = () => {
    const inbox = folderItems(folderName);
    return {
      inbox_count: inbox.length,
      inbox_selected: inbox.length === 1 && selectedFolder(inbox[0]),
    };
  };
  const hasComposer = () =>
    Array.from(
      document.querySelectorAll(
        '[contenteditable="true"],[role="textbox"],textarea,input[role="combobox"]',
      ),
    ).some((element) => {
      if (!visible(element)) return false;
      const markers = [
        "aria-label",
        "title",
        "data-testid",
        "data-automationid",
        "data-tid",
      ]
        .map((name) => element.getAttribute(name) || "")
        .join(" ");
      if (/message body|to recipients|subject|new message|compose/i.test(markers))
        return true;
      let current = element;
      while (current && current !== document.body) {
        const submit = Array.from(
          current.querySelectorAll('button,[role="button"]'),
        ).some((button) =>
          visible(button) &&
          /^(send|send now)$/i.test(
            button.getAttribute("aria-label") ||
              button.getAttribute("title") ||
              (button.innerText || "").trim(),
          ),
        );
        if (submit) return true;
        current = current.parentElement;
      }
      return false;
    });
  const timeFor = (row) => {
    const candidates = Array.from(
      row.querySelectorAll("time[datetime],[datetime],[data-timestamp],[title]"),
    )
      .filter(visible)
      .map(
        (element) =>
          element.getAttribute("datetime") ||
          element.getAttribute("data-timestamp") ||
          element.getAttribute("title") ||
          "",
      )
      .filter((value) => value && !/^\d{1,2}:\d{2}\s*(am|pm)?$/i.test(value));
    for (const value of candidates) {
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return new Date(parsed).toISOString();
    }
    return "";
  };
  const semanticText = (row, pattern) => {
    const candidates = Array.from(row.querySelectorAll("*")).filter(visible);
    const matches = candidates.filter((element) => {
      const markers = [
        "aria-label",
        "title",
        "data-testid",
        "data-automationid",
        "data-tid",
        "class",
      ]
        .map((name) => element.getAttribute(name) || "")
        .join(" ");
      return pattern.test(markers);
    });
    for (const element of matches) {
      const lines = cleanText(element);
      if (lines.length === 1) return lines[0];
    }
    return "";
  };
  const readRow = (row) => {
    const lines = cleanText(row);
    const label = row.getAttribute("aria-label") || "";
    const sender =
      semanticText(row, /\bsender\b|\bfrom\b/i) || lines[0] || label;
    const subject =
      semanticText(row, /\bsubject\b/i) ||
      (lines.length > 1 ? lines[1] : label);
    const emailNode = row.querySelector(
      '[data-email],[data-smtp-address],a[href^="mailto:"]',
    );
    let senderEmail = "";
    if (emailNode) {
      senderEmail =
        emailNode.getAttribute("data-email") ||
        emailNode.getAttribute("data-smtp-address") ||
        (emailNode.getAttribute("href") || "").replace(/^mailto:/i, "").split("?")[0];
    }
    const markers = [label, String(row.className || "")]
      .concat(
        Array.from(row.querySelectorAll("[aria-label],[title],[data-testid],[data-tid]"))
          .map((element) =>
            ["aria-label", "title", "data-testid", "data-tid"]
              .map((name) => element.getAttribute(name) || "")
              .join(" "),
          ),
      )
      .join(" ");
    const importanceLabel = /\bhigh importance\b|\bhigh priority\b/i.test(markers)
      ? "high"
      : /\blow importance\b|\blow priority\b/i.test(markers)
        ? "low"
        : "normal";
    const receivedAt = timeFor(row);
    const unread =
      /\bunread\b/i.test(markers) ||
      row.getAttribute("aria-checked") === "true" ||
      row.getAttribute("data-isread") === "false";
    const flagged = /\bflagged\b|\bunflag\b/i.test(markers) ||
      row.getAttribute("data-isflagged") === "true";
    return {
      entry_id: rowId(row),
      sender: { display_name: sender, smtp_address: senderEmail || null, recipient_type: 1 },
      subject,
      received_at: receivedAt,
      unread,
      flagged,
      importance: importanceLabel,
      version: {
        subject,
        unread,
        folder_entry_id: "inbox",
        flagged,
        importance: importanceLabel,
      },
      lines,
      aria_label: label,
    };
  };
  const getUniqueRow = (id) => {
    const matches = messageRows().filter((row) => rowId(row) === id);
    return matches.length === 1 ? matches[0] : null;
  };
  const openRow = (row, conversation) => {
    const priorBody = Array.from(document.querySelectorAll('[role="document"]')).filter(visible);
    window.__officeOutlookReadSelection = {
      conversation,
      wasSelected: row.getAttribute("aria-selected") === "true",
      priorBody: priorBody.length === 1 ? priorBody[0] : null,
      priorFingerprint: priorBody.length === 1 ? bodyFingerprint(priorBody[0]) : "",
      fingerprint: "",
      stableSince: performance.now(),
    };
    if (!window.__officeOutlookReadSelection.wasSelected) row.click();
    return { state: "opening", conversation_id: conversation };
  };
  const bodyFingerprint = (element) => {
    const text = element ? element.innerText || element.textContent || "" : "";
    let hash = 2166136261;
    for (let index = 0; index < text.length; index++)
      hash = Math.imul(hash ^ text.charCodeAt(index), 16777619);
    return `${text.length}:${hash >>> 0}`;
  };
  const normalizedFingerprint = (text) => {
    text = text.replace(/\s+/g, " ").trim();
    let hash = 2166136261;
    for (let index = 0; index < text.length; index++)
      hash = Math.imul(hash ^ text.charCodeAt(index), 16777619);
    return `${text.length}:${hash >>> 0}`;
  };
  const exactMessageNodes = (id) => {
    const attributes = [
      "data-itemid",
      "data-item-id",
      "data-messageid",
      "data-message-id",
      "data-mid",
    ];
    return Array.from(
      document.querySelectorAll(
        "[data-itemid],[data-item-id],[data-messageid],[data-message-id],[data-mid]",
      ),
    ).filter(
      (element) =>
        visible(element) &&
        attributes.some((attribute) => element.getAttribute(attribute) === id),
    );
  };
  const label = (element) =>
    (element.getAttribute("aria-label") || element.getAttribute("title") ||
      element.innerText || "").trim();
  const visibleControls = (root) =>
    Array.from(root.querySelectorAll('button,[role="button"],a[tabindex]')).filter(
      visible,
    );
  const isToField = (element, root) => {
    const labels = [
      label(element), element.getAttribute("placeholder") || "",
      ...Array.from(element.labels || [], node => node.textContent || ""),
      ...(element.getAttribute("aria-labelledby") || "").split(/\s+/)
        .map(id => document.getElementById(id)?.textContent || ""),
    ];
    if (labels.some(value => /^to(?:\b|:)/i.test(value.trim()))) return true;
    if (element.getAttribute("role") !== "combobox") return false;
    for (let ancestor = element.parentElement; ancestor && ancestor !== root.parentElement;
         ancestor = ancestor.parentElement) {
      const fields = Array.from(ancestor.querySelectorAll('input[role="combobox"]')).filter(visible);
      if (fields.length > 1) break;
      const named = Array.from(ancestor.querySelectorAll('[aria-label],[title],button,[role="button"],label,span,div'))
        .filter(visible).some(node => /^to:?$/i.test(label(node)) ||
          /^to:?$/i.test(cleanText(node).join(" ")));
      if (fields.length === 1 && named) return true;
    }
    return false;
  };
  const composerParts = (root) => {
    const editors = Array.from(
      root.querySelectorAll('[contenteditable="true"],[role="textbox"],textarea'),
    ).filter(visible);
    const bodyEditors = editors.filter((element) =>
      /message body|message content|reply body/i.test(label(element)),
    );
    const subjectFields = Array.from(
      root.querySelectorAll(
        'input[aria-label*=subject i],input[placeholder*=subject i],' +
          '[role="textbox"][aria-label*=subject i]',
      ),
    ).filter(visible);
    const recipientFields = Array.from(
      root.querySelectorAll(
        'input[role="combobox"],input[aria-label^="To" i],input[placeholder="To" i],' +
          '[role="textbox"][aria-label^="To" i]',
      ),
    ).filter(visible).filter(element => isToField(element, root));
    const sendButtons = visibleControls(root).filter((element) =>
      /^send(?:\s+now)?(?:\s*\(.*\))?$/i.test(label(element)),
    );
    return { editors, bodyEditors, subjectFields, recipientFields, sendButtons };
  };
  const composerRoots = () => {
    const candidates = Array.from(
      document.querySelectorAll(
        '[role="dialog"],[data-testid*="compose" i],[data-automationid*="compose" i]',
      ),
    );
    for (const body of Array.from(document.querySelectorAll('[contenteditable="true"]'))
      .filter(visible).filter(element => /message body|message content|reply body/i.test(label(element)))) {
      let ancestor = body.parentElement;
      while (ancestor && ancestor !== document.body) {
        const parts = composerParts(ancestor);
        if (parts.bodyEditors.length === 1 && parts.sendButtons.length === 1) {
          candidates.push(ancestor);
          break;
        }
        ancestor = ancestor.parentElement;
      }
    }
    const roots = Array.from(new Set(candidates)).filter(visible)
      .filter((root) => {
        const parts = composerParts(root);
        return parts.bodyEditors.length > 0 && parts.sendButtons.length > 0;
      });
    return roots.filter(root => !roots.some(other => other !== root && root.contains(other)));
  };
  const selectedComposer = () => {
    const roots = composerRoots();
    return roots.length === 1 ? roots[0] : null;
  };
  const selectedMessageCard = () => {
    const row = getUniqueRow(request.conversation_id);
    const bodies = Array.from(document.querySelectorAll('[role="document"]')).filter(visible);
    if (!row || row.getAttribute("aria-selected") !== "true" || bodies.length !== 1)
      return null;
    let card = bodies[0].closest('article,[role="article"]') || bodies[0].parentElement;
    const bodyCount = (scope) => bodies.filter(body => scope === body || scope.contains(body)).length;
    while (card && card !== document.body &&
      bodyCount(card) === 1 &&
      !visibleControls(card).some(element => /^(reply|reply all)$/i.test(label(element))))
      card = card.parentElement;
    return card && card !== document.body &&
      bodyCount(card) === 1
      ? card : null;
  };
  const recipientAddresses = (root) => {
    const emailPattern = /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i;
    const values = new Set();
    for (const element of root.querySelectorAll(
      "[data-email],[data-smtp-address],a[href^='mailto:'],[aria-label],[title]",
    )) {
      if (!visible(element)) continue;
      const candidates = [
        element.getAttribute("data-email") || "",
        element.getAttribute("data-smtp-address") || "",
        (element.getAttribute("href") || "").replace(/^mailto:/i, "").split("?")[0],
        element.getAttribute("aria-label") || "",
        element.getAttribute("title") || "",
        element.innerText || "",
      ];
      for (const candidate of candidates) {
        const match = candidate.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
        if (match && emailPattern.test(match[0])) values.add(match[0].toLowerCase());
      }
    }
    return Array.from(values);
  };
  const recipientEntries = (root) => {
    const emailPattern = /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i;
    const entries = new Map();
    const bodyEditors = Array.from(root.querySelectorAll('[contenteditable="true"]'))
      .filter(element => /message body|message content|reply body/i.test(label(element)));
    const elements = Array.from(
      root.querySelectorAll(
        "[data-email],[data-smtp-address],a[href^='mailto:'],[data-recipient-type]," +
          '[role="button"][aria-label*="@"],[title*="@"]',
      ),
    ).filter(visible).filter(element => !bodyEditors.some(body => body.contains(element)));
    for (const element of elements) {
      const rawAddress =
        element.getAttribute("data-email") ||
        element.getAttribute("data-smtp-address") ||
        (element.getAttribute("href") || "").replace(/^mailto:/i, "").split("?")[0] ||
        element.getAttribute("title") || element.getAttribute("aria-label") || "";
      const addresses = rawAddress.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || [];
      if (addresses.length !== 1) continue;
      const address = addresses[0];
      if (!emailPattern.test(address)) continue;
      const markers = [
        element.getAttribute("aria-label") || "",
        element.getAttribute("title") || "",
        element.getAttribute("data-recipient-type") || "",
        element.getAttribute("data-type") || "",
      ].join(" ");
      const rawType = Number(
        element.getAttribute("data-recipient-type") ||
          element.getAttribute("data-type") ||
          0,
      );
      const recipientType =
        rawType >= 1 && rawType <= 3
          ? rawType
          : /\bbcc\b/i.test(markers)
            ? 3
            : /\bcc\b/i.test(markers)
              ? 2
              : 1;
      const key = `${recipientType}:${address.toLowerCase()}`;
      if (!entries.has(key)) {
        entries.set(key, {
          display_name:
            element.getAttribute("data-display-name") ||
            (element.innerText || "").trim() ||
            address,
          smtp_address: address.toLowerCase(),
          recipient_type: recipientType,
        });
      }
    }
    return Array.from(entries.values());
  };
  const draftIdentity = (root) => {
    const attributes = [
      "data-itemid",
      "data-item-id",
      "data-messageid",
      "data-message-id",
      "data-mid",
    ];
    const candidates = [root, ...Array.from(root.querySelectorAll("*"))].flatMap(
      (element) =>
        attributes.map((attribute) => element.getAttribute(attribute) || ""),
    );
    const ids = Array.from(new Set(candidates.filter(Boolean)));
    const conversations = [root, ...Array.from(root.querySelectorAll("[data-convid]"))]
      .filter((element) => element.hasAttribute("data-convid"))
      .map((element) => element.getAttribute("data-convid") || "")
      .filter(Boolean);
    return {
      entry_id: ids.length === 1 ? ids[0] : "",
      conversation_id:
        new Set(conversations).size === 1 ? conversations[0] : "",
      candidate_count: ids.length,
    };
  };
  const composeSnapshot = (root) => {
    if (!root) return { state: "composer_unavailable" };
    const parts = composerParts(root);
    if (
      parts.bodyEditors.length !== 1 ||
      parts.subjectFields.length > 1 ||
      parts.recipientFields.length > 1
    )
      return { state: "composer_ambiguous" };
    const body = parts.bodyEditors[0];
    const subject = parts.subjectFields[0] || null;
    const identity = draftIdentity(root);
    const saved = Array.from(root.querySelectorAll("[aria-label],[title],[data-testid],[role=status]"))
      .filter(visible)
      .some((element) => /\bsaved\b/i.test(label(element)) &&
        !/\bsaving\b/i.test(label(element)));
    return {
      state: "composer_ready",
      subject: subject ? subject.value || subject.innerText || "" : "",
      body_text: body.innerText || body.value || "",
      recipients: recipientEntries(root),
      saved,
      entry_id: identity.entry_id,
      conversation_id: identity.conversation_id,
      draft_identity_candidate_count: identity.candidate_count,
      attachment_count: root.querySelectorAll(
        '[data-testid*="attachment" i],[data-automationid*="attachment" i]',
      ).length,
    };
  };
  const composerToken = (value) => {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (/^to(?:\b|:)/i.test(text)) return "to";
    if (/^cc(?:\b|:)/i.test(text)) return "cc";
    if (/^bcc(?:\b|:)/i.test(text)) return "bcc";
    if (/^subject\b/i.test(text)) return "subject";
    if (/\bsearch\b/i.test(text)) return "search";
    if (/\brecipient/i.test(text)) return "recipient";
    if (/\bemail\b/i.test(text)) return "email";
    if (/\baddress\b/i.test(text)) return "address";
    if (/\bname\b/i.test(text)) return "name";
    if (/\bsaving\b/i.test(text)) return "saving";
    if (/\bsaved\b/i.test(text)) return "saved";
    return "";
  };
  const composerFieldEvidence = (element, root) => {
    const relationNodes = (attribute) =>
      (element.getAttribute(attribute) || "").split(/\s+/).filter(Boolean)
        .map(id => document.getElementById(id)).filter(Boolean);
    const categoryList = (values) => Array.from(new Set(
      values.map(composerToken).filter(Boolean),
    ));
    const directLabels = [
      label(element),
      element.getAttribute("placeholder") || "",
    ];
    const associatedLabels = Array.from(element.labels || [], label);
    const labelledBy = relationNodes("aria-labelledby").map(label);
    const describedBy = relationNodes("aria-describedby").map(label);
    return {
      role: element.getAttribute("role") || "",
      label_categories: categoryList(directLabels),
      associated_label_count: associatedLabels.length,
      associated_label_categories: categoryList(associatedLabels),
      labelledby_count: labelledBy.length,
      labelledby_categories: categoryList(labelledBy),
      describedby_count: describedBy.length,
      describedby_categories: categoryList(describedBy),
      in_composer: root.contains(element),
      search_scope: !!element.closest('[role="search"]'),
      to_relation: isToField(element, root),
    };
  };
  const composerProbeSnapshot = (root, snapshot) => {
    const parts = composerParts(root);
    const subject = typeof snapshot.subject === "string" ? snapshot.subject : "";
    const bodyText = typeof snapshot.body_text === "string" ? snapshot.body_text : "";
    const recipients = Array.isArray(snapshot.recipients) ? snapshot.recipients : [];
    const controls = Array.from(
      document.querySelectorAll('input[role="combobox"]'),
    ).filter(visible).slice(0, 12).map(element => composerFieldEvidence(element, root));
    const savedMarkers = Array.from(root.querySelectorAll(
      "[aria-label],[title],[data-testid],[role=status]",
    )).filter(visible).filter(element =>
      /\bsaved\b/i.test(label(element)) && !/\bsaving\b/i.test(label(element)),
    );
    const outsideSavedMarkers = Array.from(document.querySelectorAll(
      "[aria-label],[title],[data-testid],[role=status]",
    )).filter(visible).filter(element =>
      !root.contains(element) && /\bsaved\b/i.test(label(element)) &&
        !/\bsaving\b/i.test(label(element)),
    );
    const recipientTypeCounts = { to: 0, cc: 0, bcc: 0 };
    for (const recipient of recipients) {
      const type = Number(recipient.recipient_type);
      if (type >= 1 && type <= 3)
        recipientTypeCounts[["", "to", "cc", "bcc"][type]]++;
    }
    return {
      state: snapshot.state,
      subject_chars: subject.length,
      subject_empty: !subject.trim(),
      body_chars: bodyText.length,
      body_whitespace_only: !bodyText.trim(),
      recipient_count: recipients.length,
      recipient_type_counts: recipientTypeCounts,
      recipient_chip_candidate_count: Array.from(root.querySelectorAll(
        "[data-email],[data-smtp-address],a[href^='mailto:'],[data-recipient-type]," +
          '[role="button"][aria-label*="@"],[title*="@"]',
      )).filter(visible).length,
      attachment_count: snapshot.attachment_count || 0,
      saved: snapshot.saved === true,
      saved_marker_count_in_composer: savedMarkers.length,
      saved_marker_count_outside_composer: outsideSavedMarkers.length,
      draft_identity_candidate_count: snapshot.draft_identity_candidate_count || 0,
      draft_identity_present: !!snapshot.entry_id,
      conversation_identity_present: !!snapshot.conversation_id,
      body_editor_count: parts.bodyEditors.length,
      subject_field_count: parts.subjectFields.length,
      to_field_count: parts.recipientFields.length,
      visible_combobox_count: controls.length,
      recipient_controls_outside_composer: controls.filter(control =>
        !control.search_scope && !control.in_composer,
      ).length,
      recipient_controls_without_to_relation: controls.filter(control =>
        !control.search_scope && !control.to_relation,
      ).length,
      recipient_controls: controls,
      reply_marker_count: Array.from(root.querySelectorAll(
        "[aria-label],[title],[data-testid],[data-automationid]",
      )).filter(visible).filter(element =>
        /\breply\b/i.test(
          (element.getAttribute("aria-label") || "") + " " +
          (element.getAttribute("title") || "") + " " +
          (element.getAttribute("data-testid") || "") + " " +
          (element.getAttribute("data-automationid") || ""),
        ),
      ).length,
    };
  };
  const inspectSelectedMessageIdentity = (openDetails) => {
    if (hasComposer()) {
      if (openDetails) return { state: "draft_exists" };
      const root = selectedComposer();
      const snapshot = root ? composeSnapshot(root) : null;
      return {
        state: "composer_inspected",
        root_count: composerRoots().length,
        composer: root && snapshot
          ? composerProbeSnapshot(root, snapshot)
          : { state: snapshot ? snapshot.state : "composer_unavailable" },
      };
    }
    const regions = Array.from(
      document.querySelectorAll(
        '[role="document"],[data-testid*="message-body" i],' +
          '[data-automationid*="message-body" i],[data-testid*="messagecontent" i]',
      ),
    ).filter(visible);
    const region = regions[0] || null;
    const card = region
      ? region.closest('article,[role="article"]') || region.parentElement || region
      : null;
    const rows = messageRows().filter(
      (row) =>
        row.getAttribute("aria-selected") === "true" ||
        row.getAttribute("aria-current") === "true",
    );
    if (!card) return { state: "message_detail_unavailable", selected_row_count: rows.length };
    const isSafeScope = (scope) => {
      const role = scope.getAttribute("role") || "";
      const marker = [
        scope.id,
        scope.getAttribute("data-testid"),
        scope.getAttribute("data-automationid"),
      ].join(" ");
      if (
        scope === document.body ||
        /^(main|navigation|tree|listbox)$/i.test(role) ||
        /^(MAIN|NAV)$/i.test(scope.tagName) ||
        /sidebar|navigation|folder[-_ ]?list|message[-_ ]?list|conversation[-_ ]?list/i.test(marker)
      ) return false;
      if (regions.filter((candidate) => scope.contains(candidate)).length !== 1)
        return false;
      if (messageRows().some((candidate) => scope.contains(candidate)) ||
          treeItems().some((candidate) => scope.contains(candidate)))
        return false;
      return !Array.from(scope.querySelectorAll(
        '[contenteditable="true"],[role="textbox"],textarea',
      )).some(visible);
    };
    let scope = null;
    let ancestorDepth = 0;
    for (let candidate = card, depth = 0; candidate && depth <= 4; candidate = candidate.parentElement, depth++) {
      if (!isSafeScope(candidate)) break;
      scope = candidate;
      ancestorDepth = depth;
    }
    if (!scope)
      return { state: "message_detail_ambiguous", selected_row_count: rows.length };
    const nodes = [scope, ...Array.from(scope.querySelectorAll("*"))];
    const attributeCounts = {};
    for (const attribute of [
      "data-itemid",
      "data-item-id",
      "data-messageid",
      "data-message-id",
      "data-mid",
      "data-convid",
      "data-id",
      "data-automationid",
      "id",
    ]) {
      attributeCounts[attribute] = nodes.filter((element) =>
        !!element.getAttribute(attribute),
      ).length;
    }
    const links = Array.from(scope.querySelectorAll("a[href]")).filter(visible);
    let sameOriginMessageLinkCount = 0;
    let itemIdQueryKeyCount = 0;
    let messageIdQueryKeyCount = 0;
    for (const link of links) {
      try {
        const url = new URL(link.href, location.href);
        if (
          url.origin === location.origin &&
          /\/mail(?:\/|$)/i.test(url.pathname)
        ) {
          sameOriginMessageLinkCount++;
          for (const key of url.searchParams.keys()) {
            if (/^(itemid|item_id)$/i.test(key)) itemIdQueryKeyCount++;
            if (/^(messageid|message_id|internetmessageid)$/i.test(key))
              messageIdQueryKeyCount++;
          }
        }
      } catch {}
    }
    const controls = visibleControls(scope);
    const exactLabel = (element, pattern) =>
      [element.getAttribute("aria-label"), element.getAttribute("title"), element.innerText]
        .some((text) => pattern.test((text || "").trim()));
    const detailsPattern = /^(view message details|message details|internet headers|view source)$/i;
    const menuSurfaces = Array.from(document.querySelectorAll('[role="menu"]')).filter(visible);
    const probe = window.__officeIdentityProbe || {};
    const newMenus = probe.menusBefore
      ? menuSurfaces.filter((surface) => !probe.menusBefore.includes(surface))
      : [];
    const menuControls = newMenus.length === 1
      ? Array.from(newMenus[0].querySelectorAll('button,[role="button"],[role="menuitem"]'))
          .filter(visible)
      : [];
    const detailsControls = controls.filter((element) => exactLabel(element, detailsPattern));
    const menuDetailsControls = menuControls.filter((element) =>
      exactLabel(element, detailsPattern),
    );
    const detailsTargets = Array.from(new Set([...detailsControls, ...menuDetailsControls]));
    const moreActions = controls.filter((element) =>
      exactLabel(element, /^more actions$/i),
    );
    let state = "inspected";
    if (openDetails) {
      if (hasComposer()) {
        state = "draft_exists";
      } else if (rows.length !== 1 || regions.length !== 1) {
        state = "message_detail_ambiguous";
      } else if (detailsTargets.length === 1) {
        probe.dialogsBefore = Array.from(document.querySelectorAll('[role="dialog"]')).filter(visible);
        detailsTargets[0].click();
        window.__officeIdentityProbe = probe;
        state = "details_opened";
      } else if (!probe.menusBefore && moreActions.length === 1) {
        probe.menusBefore = menuSurfaces;
        window.__officeIdentityProbe = probe;
        moreActions[0].click();
        state = "more_actions_opened";
      } else {
        state = "details_unavailable";
      }
    }
    const detailsSurfaces = probe.dialogsBefore
      ? Array.from(document.querySelectorAll('[role="dialog"]')).filter(
          (surface) => visible(surface) && !probe.dialogsBefore.includes(surface),
        )
      : [];
    let internetMessageIdLabelCount = 0;
    const internetMessageIds = new Set();
    for (const surface of detailsSurfaces) {
      const labels = Array.from(
        surface.querySelectorAll("dt,[role=term],[data-testid],[aria-label]"),
      ).filter(visible);
      for (const element of labels) {
        if (
          !/^internet message[- ]?id$/i.test(
            (element.innerText || element.getAttribute("aria-label") || "").trim(),
          )
        )
          continue;
        internetMessageIdLabelCount++;
        const valueElement =
          element.nextElementSibling ||
          element.parentElement &&
            element.parentElement.querySelector("dd,[role=definition]");
        const value = valueElement
          ? (valueElement.innerText || valueElement.textContent || "").trim()
          : "";
        if (value) internetMessageIds.add(value);
      }
    }
    return {
      state,
      selected_row_count: rows.length,
      mail_row_count: messageRows().length,
      duplicate_conversation_row_count: messageRows().length -
        new Set(messageRows().map(rowId)).size,
      read_selection_conversation_row_count: messageRows().filter(row =>
        rowId(row) === window.__officeOutlookReadSelection?.conversation).length,
      body_region_count: regions.length,
      safe_ancestor_depth: ancestorDepth,
      identity_attribute_counts: attributeCounts,
      link_count: links.length,
      same_origin_message_link_count: sameOriginMessageLinkCount,
      item_id_query_key_count: itemIdQueryKeyCount,
      message_id_query_key_count: messageIdQueryKeyCount,
      details_control_count: detailsControls.length,
      menu_details_control_count: menuDetailsControls.length,
      menu_surface_count: menuSurfaces.length,
      new_menu_count: newMenus.length,
      details_dialog_count: detailsSurfaces.length,
      more_actions_control_count: moreActions.length,
      internet_message_id_label_count: internetMessageIdLabelCount,
      internet_message_id_count: internetMessageIds.size,
    };
  };
  const setValue = (element, value) => {
    if (!(element instanceof HTMLInputElement) && !(element instanceof HTMLTextAreaElement)) {
      element.innerText = value;
      element.dispatchEvent(
        new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }),
      );
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const prototype =
      element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value").set;
    setter.call(element, value);
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const setBody = (element, value) => {
    element.innerText = value;
    element.dispatchEvent(
      new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }),
    );
    element.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const scrollContainer = () => {
    const row = messageRows()[0];
    if (!row) return null;
    const candidates = [row, ...Array.from((function* () {
      let current = row.parentElement;
      while (current && current !== document.body) {
        yield current;
        current = current.parentElement;
      }
    })())];
    return (
      candidates.find(
        (element) =>
          element.scrollHeight > element.clientHeight + 8 &&
          /^(auto|scroll)$/.test(getComputedStyle(element).overflowY),
      ) || null
    );
  };

  if (request.phase === "scan") {
    const folder = folderState();
    if (folder.inbox_count !== 1) return { state: "inbox_ambiguous", rows: [] };
    if (!folder.inbox_selected) {
      if (hasComposer()) return { state: "draft_exists", rows: [] };
      folderItems(folderName)[0].click();
      return { state: "navigating_inbox", rows: [] };
    }
    if (request.open_conversation_id) {
      if (hasComposer()) return { state: "draft_exists" };
      const matches = messageRows().filter(row => rowId(row) === request.open_conversation_id);
      if (matches.length > 1) return { state: "message_identity_ambiguous" };
      if (matches.length === 1) return openRow(matches[0], request.open_conversation_id);
    }
    const container = scrollContainer();
    let scrollAdvanced = false;
    if (request.step === "start" && container) {
      window.__officeOutlookScanScroll = container.scrollTop;
      container.scrollTop = 0;
    } else if (request.step === "next" && container) {
      const priorTop = container.scrollTop;
      container.scrollTop = Math.min(
        container.scrollHeight,
        container.scrollTop + Math.max(1, Math.floor(container.clientHeight * 0.8)),
      );
      scrollAdvanced = container.scrollTop > priorTop;
    } else if (request.step === "restore" && container) {
      container.scrollTop = Number(window.__officeOutlookScanScroll || 0);
      delete window.__officeOutlookScanScroll;
    }
    const rows = messageRows().map(readRow).filter((row) => row.entry_id);
    return {
      state: "listed",
      rows,
      row_count: rows.length,
      at_bottom: !container || container.scrollTop + container.clientHeight >= container.scrollHeight - 4,
      has_scroll_container: !!container,
      scroll_advanced: scrollAdvanced,
      inbox_selected: folderState().inbox_selected,
    };
  }

  if (request.phase === "open") {
    if (hasComposer()) return { state: "draft_exists" };
    const row = getUniqueRow(request.conversation_id);
    if (!row)
      return {
        state: messageRows().length ? "not_visible" : "message_not_found",
      };
    return openRow(row, request.conversation_id);
  }

  if (request.phase === "read") {
    const row = getUniqueRow(request.conversation_id);
    if (!row && !request.exact_message_navigation) return { state: "message_not_found" };
    const active = request.exact_message_navigation ||
      row.getAttribute("aria-selected") === "true" ||
      row.getAttribute("aria-current") === "true";
    if (!active) return { state: "waiting" };
    const regions = Array.from(
      document.querySelectorAll(
        '[role="document"],[data-testid*="message-body" i],' +
          '[data-automationid*="message-body" i],[data-testid*="messagecontent" i]',
      ),
    ).filter(visible);
    const bodyRegions = regions.filter((element) =>
      (element.innerText || element.textContent || "").trim(),
    );
    if (!bodyRegions.length) return { state: "waiting" };
    if (bodyRegions.length !== 1) return { state: "message_identity_ambiguous" };
    let bodyRegion = bodyRegions[0];
    if (Array.isArray(request.body_fingerprints)) {
      if (!request.body_fingerprints.length) return { state: "waiting" };
      const matches = [bodyRegion, ...bodyRegion.querySelectorAll("div,table,section,p")]
        .filter(visible).filter(element => request.body_fingerprints.includes(
          normalizedFingerprint(element.textContent || ""),
        ));
      const roots = matches.filter(element =>
        !matches.some(other => other !== element && other.contains(element)));
      if (!roots.length) return { state: "waiting" };
      if (roots.length !== 1) return { state: "message_identity_ambiguous" };
      bodyRegion = roots[0];
    }
    const selection = window.__officeOutlookReadSelection;
    if (selection && selection.conversation === request.conversation_id) {
      const fingerprint = bodyFingerprint(bodyRegion);
      if (!selection.wasSelected && selection.priorBody === bodyRegion &&
          selection.priorFingerprint === fingerprint)
        return { state: "waiting" };
      if (selection.fingerprint !== fingerprint) {
        selection.fingerprint = fingerprint;
        selection.stableSince = performance.now();
        return { state: "waiting" };
      }
      if (performance.now() - selection.stableSince < 400)
        return { state: "waiting" };
    }
    const body = (bodyRegion.innerText || bodyRegion.textContent || "").trim();
    const card =
      bodyRegion.closest('article,[role="article"]') ||
      bodyRegion.parentElement ||
      bodyRegion;
    const identityAttributes = [
      "data-itemid",
      "data-item-id",
      "data-messageid",
      "data-message-id",
      "data-mid",
    ];
    const identityElements = [card, ...Array.from(card.querySelectorAll("*"))];
    const identities = Array.from(
      new Set(
        identityElements.flatMap((element) =>
          identityAttributes
            .map((attribute) => element.getAttribute(attribute) || "")
            .filter(Boolean),
        ),
      ),
    );
    // The DOM rarely exposes an item identity. The host binds the exact message
    // from Outlook's own conversation response; a DOM identity only adds a check.
    if (
      identities.length > 1 ||
      (identities.length === 1 && request.message_id && identities[0] !== request.message_id)
    ) {
      return {
        state: "message_identity_ambiguous",
        identity_candidate_count: identities.length,
      };
    }
    const messageId = identities.length === 1 ? identities[0] : "";
    const summary = row ? readRow(row) : null;
    return {
      state: "read",
      item: summary,
      dom_message_id: messageId,
      identity_candidate_count: identities.length,
      body_text: body.slice(0, 20001),
      body_binding_text: (bodyRegion.textContent || "").slice(0, 200000),
      body_truncated: body.length > 20000,
      recipients: recipientEntries(card),
    };
  }

  if (request.phase === "reply") {
    if (hasComposer()) return { state: "draft_exists" };
    const matches = exactMessageNodes(request.message_id);
    let card = matches.length === 1
      ? matches[0].closest('article,[role="article"],[role="document"]') ||
        matches[0].parentElement : null;
    if (!card && matches.length === 0 && request.conversation_id) {
      card = selectedMessageCard();
    }
    if (matches.length > 1) return { state: "message_identity_ambiguous" };
    if (!card) return { state: "message_not_found" };
    const expected = request.reply_all
      ? /^reply\s+all$/i
      : /^reply$/i;
    const controls = visibleControls(card).filter((element) =>
      expected.test(label(element)),
    );
    if (controls.length !== 1)
      return { state: controls.length ? "reply_control_ambiguous" : "reply_unavailable" };
    controls[0].click();
    return { state: "composer_opening" };
  }

  if (request.phase === "new_mail") {
    if (hasComposer()) return { state: "draft_exists" };
    const controls = visibleControls(document).filter((element) =>
      /^(new mail|compose)$/i.test(label(element)),
    );
    if (controls.length !== 1)
      return { state: controls.length ? "compose_control_ambiguous" : "compose_unavailable" };
    controls[0].click();
    return { state: "composer_opening" };
  }

  if (request.phase === "reply_capability") {
    const card = hasComposer() ? null : selectedMessageCard();
    return {
      state: "reply_capability",
      available: !!card &&
        visibleControls(card).filter(element => /^reply$/i.test(label(element))).length === 1 &&
        visibleControls(card).filter(element => /^reply all$/i.test(label(element))).length === 1,
    };
  }

  if (request.phase === "composer_state") {
    const roots = composerRoots();
    if (!roots.length) return { state: "waiting" };
    if (roots.length !== 1) return { state: "composer_ambiguous" };
    return composeSnapshot(roots[0]);
  }

  if (request.phase === "fill_draft") {
    const root = selectedComposer();
    if (!root) return { state: composerRoots().length ? "composer_ambiguous" : "composer_unavailable" };
    const parts = composerParts(root);
    if (parts.bodyEditors.length !== 1) return { state: "composer_ambiguous" };
    const snapshot = composeSnapshot(root);
    if (snapshot.state !== "composer_ready") return snapshot;
    if (request.recipient_email) {
      if (parts.recipientFields.length !== 1 || snapshot.recipients.length)
        return { state: "recipient_field_unavailable" };
      setValue(parts.recipientFields[0], request.recipient_email);
    }
    if (request.subject !== null && request.subject !== undefined) {
      if (parts.subjectFields.length !== 1) return { state: "subject_field_unavailable" };
      setValue(parts.subjectFields[0], request.subject);
    }
    if (typeof request.body_text === "string")
      setBody(parts.bodyEditors[0], request.body_text);
    return { state: "draft_filled" };
  }

  if (request.phase === "select_recipient") {
    const root = selectedComposer();
    if (!root) return { state: "composer_unavailable" };
    const emailPattern = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
    const matches = Array.from(
      document.querySelectorAll('[role="option"],[role="listbox"] [role="option"]'),
    )
      .filter(visible)
      .filter((element) => {
        const emails = (label(element).match(emailPattern) || []).map((email) =>
          email.toLowerCase(),
        );
        return (
          emails.length === 1 &&
          emails[0] === request.recipient_email.toLowerCase()
        );
      });
    if (matches.length !== 1)
      return { state: matches.length ? "recipient_ambiguous" : "unresolved_recipient" };
    matches[0].click();
    return { state: "recipient_selected" };
  }

  if (request.phase === "close_saved_draft") {
    const root = selectedComposer();
    if (!root) return { state: "composer_unavailable" };
    const snapshot = composeSnapshot(root);
    if (snapshot.state !== "composer_ready" || !snapshot.saved)
      return { state: "draft_not_saved" };
    if (
      request.host_entry_id &&
      (snapshot.draft_identity_candidate_count !== 1 ||
        snapshot.entry_id !== request.host_entry_id)
    )
      return { state: "draft_identity_unavailable" };
    if (
      !request.host_identity_verified &&
      (snapshot.draft_identity_candidate_count !== 1 || !snapshot.entry_id)
    )
      return { state: "draft_identity_unavailable" };
    const closeButtons = visibleControls(root).filter((element) =>
      /^(close|close compose)$/i.test(label(element)),
    );
    if (closeButtons.length !== 1)
      return { state: closeButtons.length ? "composer_ambiguous" : "draft_not_saved" };
    closeButtons[0].click();
    return { state: "draft_saved", draft: snapshot };
  }

  if (request.phase === "send") {
    const root = selectedComposer();
    if (!root) return { state: "composer_unavailable" };
    const snapshot = composeSnapshot(root);
    if (snapshot.state !== "composer_ready" ||
      snapshot.body_text !== request.body_text || snapshot.subject !== request.subject ||
      snapshot.attachment_count !== 0 ||
      JSON.stringify(snapshot.recipients) !== JSON.stringify(request.recipients))
      return { state: "stale_draft" };
    const buttons = composerParts(root).sendButtons;
    if (buttons.length !== 1) return { state: "composer_ambiguous" };
    buttons[0].click();
    return { state: "send_clicked" };
  }

  if (request.phase === "archive") {
    if (hasComposer()) return { state: "draft_exists" };
    const row = getUniqueRow(request.conversation_id);
    if (!row || row.getAttribute("aria-selected") !== "true")
      return { state: "message_not_found" };
    const buttons = visibleControls(document).filter(element => /^archive$/i.test(label(element)));
    if (buttons.length !== 1) return { state: "archive_unavailable" };
    buttons[0].click();
    return { state: "archive_clicked" };
  }

  if (request.phase === "attachment_list" || request.phase === "download_attachment") {
    if (hasComposer()) return { state: "draft_exists" };
    const row = getUniqueRow(request.conversation_id);
    if ((!row || row.getAttribute("aria-selected") !== "true") && !request.exact_message_navigation)
      return { state: "message_not_found" };
    const bodies = Array.from(document.querySelectorAll('[role="document"]')).filter(visible);
    if (bodies.length !== 1) return { state: "message_identity_ambiguous" };
    if (request.phase === "download_attachment" && request.filename) {
      const pending = window.__officeAttachmentDownload;
      if (pending && pending.id === request.attachment_id &&
        pending.conversation === request.conversation_id) {
        const menus = Array.from(document.querySelectorAll('[role="menuitem"]'))
          .filter(visible).filter(element => /^download$/i.test(label(element)));
        if (menus.length !== 1) return { state: "attachment_unavailable" };
        delete window.__officeAttachmentDownload;
        menus[0].click();
        return { state: "download_clicked" };
      }
      const markers = Array.from(document.querySelectorAll(
        '[data-testid*="attachment" i],[data-automationid*="attachment" i],' +
          '[role="button"][aria-label],[title]',
      )).filter(visible).filter(element =>
        label(element) === request.filename || label(element).includes(request.filename));
      const tiles = [];
      for (const marker of markers) {
        let tile = marker;
        while (tile && tile !== document.body &&
          !visibleControls(tile).some(element =>
            /^download(?:\s|$)|^(more actions|more options|attachment options)$/i.test(label(element))))
          tile = tile.parentElement;
        if (tile && tile !== document.body &&
          Array.from(tile.querySelectorAll('[role="document"]')).filter(visible).length <= 1)
          tiles.push(tile);
      }
      const unique = Array.from(new Set(tiles));
      if (unique.length !== 1) return { state: "attachment_unavailable" };
      const downloads = visibleControls(unique[0]).filter(element =>
        /^download(?:\s|$)/i.test(label(element)));
      if (downloads.length === 1) {
        downloads[0].click();
        return { state: "download_clicked" };
      }
      const options = visibleControls(unique[0]).filter(element =>
        /^(more actions|more options|attachment options)$/i.test(label(element)));
      if (options.length !== 1) return { state: "attachment_unavailable" };
      window.__officeAttachmentDownload = {
        id: request.attachment_id, conversation: request.conversation_id,
      };
      options[0].click();
      return { state: "attachment_menu_opening" };
    }
    let scope = bodies[0].parentElement;
    while (scope && scope !== document.body &&
      !scope.querySelector('[data-attachmentid],[data-attachment-id]')) scope = scope.parentElement;
    if (!scope || scope === document.body)
      return { state: "attachment_list", items: [] };
    const elements = Array.from(scope.querySelectorAll('[data-attachmentid],[data-attachment-id]'));
    const items = elements.filter(visible).map((element, index) => ({
      index: index + 1,
      attachment_id: element.getAttribute("data-attachmentid") ||
        element.getAttribute("data-attachment-id"),
      filename: element.getAttribute("data-filename") || label(element),
    }));
    if (request.phase === "attachment_list") return { state: "attachment_list", items };
    const matches = elements.filter(element =>
      (element.getAttribute("data-attachmentid") || element.getAttribute("data-attachment-id")) ===
      request.attachment_id);
    if (matches.length !== 1) return { state: "attachment_unavailable" };
    const buttons = visibleControls(matches[0]).filter(element => /^download$/i.test(label(element)));
    if (buttons.length !== 1) return { state: "attachment_unavailable" };
    buttons[0].click();
    return { state: "download_clicked" };
  }

  if (request.phase === "identity_probe")
    return inspectSelectedMessageIdentity(request.open_details === true);

  if (request.phase === "selected_conversation") {
    const selected = messageRows().filter(
      (row) =>
        row.getAttribute("aria-selected") === "true" ||
        row.getAttribute("aria-current") === "true",
    );
    const conversation = selected.length === 1
      ? selected[0].getAttribute("data-convid") || ""
      : "";
    return conversation
      ? { state: "selected", conversation_id: conversation }
      : { state: "selection_ambiguous", selected_count: selected.length };
  }

  return { state: "invalid_phase" };
})()
